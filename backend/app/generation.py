"""Section 2 Step C: generate the shortfall for one allocation line.

Retrieves grounding chunk(s) for the topic from Section 1's vector store,
builds the type-specific prompt (tasks 4-6), calls the LLM through the
reliability wrapper (task 7), validates against the schema (retrying on
malformed output, SPEC section 9), and dedup/clusters + banks each
accepted question (tasks 8-10). This is the real generate_fn that
quiz_engine.assemble_quiz's mock was standing in for.

Task 14 finding: a naive sequential loop over `count` individual LLM
calls doesn't hit the 10-20s latency target once count gets large (25
questions x ~1-2s each is 25-50s). The LLM calls themselves have no
shared state, so they parallelize safely via task 10b's generate_parallel;
the DB writes (dedup + intake) do NOT — psycopg connections aren't safe
for concurrent use from multiple threads — so those stay sequential on
the caller's connection after all the parallel generation finishes. DB
writes are ~10-20ms each, negligible next to LLM latency, so this still
gets the real speedup where it matters.
"""

from __future__ import annotations

import os
import random
from pathlib import Path
from typing import Callable

import chromadb
import psycopg

from app.background import generate_parallel
from app.dedup import intake_question
from app.llm_client import Provider, generate_with_reliability
from app.prompts import PROMPT_BUILDERS, SchemaValidationError, parse_and_validate
from app.scope_resolution import AllocationLine

_DEFAULT_CHROMA_PATH = Path(__file__).resolve().parent.parent.parent / "ingestion" / "chroma_db"
CHROMA_PATH = Path(os.environ.get("CHROMA_DB_PATH", str(_DEFAULT_CHROMA_PATH)))


def get_grounding_chunks(class_: str, subject: str, topic: str, n: int = 5) -> list[dict]:
    """Metadata-filtered retrieval from Section 1's Chroma store (PLAN.md
    section 2 Step C, item 1)."""
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    coll = client.get_collection("chunks")
    res = coll.get(
        where={"$and": [{"class": class_}, {"subject": subject}, {"topic": topic}]},
        include=["documents", "metadatas"],
        limit=n,
    )
    return [{"text": doc, "chunk_id": meta["chunk_id"]} for doc, meta in zip(res["documents"], res["metadatas"])]


def get_wording_style_example(conn: psycopg.Connection, class_: str, subject: str, chapter: str) -> str | None:
    """SPEC.md section 6: a real NCERT-authored exercise question's
    wording, used as a grade-level phrasing reference at generation time —
    NOT a fact source (the model must not copy its content, only its
    grammar/complexity level). Prefers a source='ncert_activity' row (this
    is exactly what that source exists for); falls back to any row in the
    chapter, since real NCERT wording at the right grade level is useful
    for this purpose regardless of source. Best-effort: returns None for a
    chapter with no reference_corpus entries at all, same as
    explanations.py's existing style-reference lookup."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT question FROM reference_corpus
            WHERE class = %s AND subject = %s AND chapter = %s
            ORDER BY (source = 'ncert_activity') DESC, random()
            LIMIT 1
            """,
            (class_, subject, chapter),
        )
        row = cur.fetchone()
        return row["question"] if row else None


def _generate_one_validated_question(
    chunk: dict,
    question_type: str,
    difficulty: str,
    provider: Provider,
    fallback_provider: Provider | None,
    max_attempts: int,
    style_example: str | None = None,
):
    """LLM call + schema validation only — no DB access, safe to run in a
    worker thread alongside others."""
    for _attempt in range(max_attempts):
        prompt = PROMPT_BUILDERS[question_type](chunk["text"], difficulty, style_example)
        raw = generate_with_reliability(prompt, primary=provider, fallback=fallback_provider, max_retries=1)
        try:
            payload = parse_and_validate(question_type, raw)
            return {"payload": payload, "chunk_id": chunk["chunk_id"]}
        except SchemaValidationError:
            continue  # SPEC section 9: malformed output -> regenerate (bounded)
    return None  # SPEC section 9: persistent failure -> drop


def make_generate_fn(
    conn: psycopg.Connection,
    class_: str,
    subject: str,
    chapter: str,
    provider: Provider,
    fallback_provider: Provider | None,
    embed_model,
    max_attempts: int = 3,
) -> Callable[[AllocationLine, int], list[dict]]:
    """Returns a generate_fn compatible with quiz_engine.assemble_quiz."""
    # Fetched once per quiz (not per question/chunk) — it's the same
    # chapter-level style example for every question generated in this
    # call, not something that varies per sub-unit or per round.
    style_example = get_wording_style_example(conn, class_, subject, chapter)

    def generate_fn(line: AllocationLine, count: int) -> list[dict]:
        chunks = get_grounding_chunks(class_, subject, line.sub_unit)
        if not chunks:
            raise RuntimeError(f"no Section 1 chunks found for {class_}/{subject}/{line.sub_unit}")

        # SPEC section 9 / section 12: "drop it and generate a
        # replacement, so the user still receives the exact number of
        # questions requested" — task 14 finding: a single top-up-free
        # batch can come up short (a schema-validation failure exhausts
        # its retries, or an exact-hash duplicate gets rejected at
        # intake), so this loops — generating the remaining shortfall,
        # still in parallel (task 10b) — until `count` is met or a
        # bounded number of rounds is exhausted (a genuinely
        # content-starved topic shouldn't loop forever).
        created: list[dict] = []
        remaining = count
        for _round in range(max_attempts):
            if remaining <= 0:
                break

            chosen_chunks = [random.choice(chunks) for _ in range(remaining)]
            outcome = generate_parallel(
                chosen_chunks,
                lambda chunk: _generate_one_validated_question(
                    chunk, line.question_type, line.difficulty, provider, fallback_provider, max_attempts, style_example
                ),
            )

            # DB writes happen sequentially, back on the caller's connection.
            for i in sorted(outcome.results.keys()):
                result_data = outcome.results[i]
                if result_data is None:
                    continue
                payload = result_data["payload"]
                question_text = payload.question
                embedding = embed_model.encode(question_text).tolist()
                intake_result = intake_question(
                    conn,
                    class_=class_,
                    subject=subject,
                    chapter=chapter,
                    topic=line.sub_unit,
                    difficulty=line.difficulty,
                    question_type=line.question_type,
                    text=question_text,
                    payload=payload.model_dump(),
                    source_chunk_id=result_data["chunk_id"],
                    embedding=embedding,
                )
                if intake_result.accepted:
                    created.append({"question_id": intake_result.question_id, "payload": payload})
                # exact_duplicate -> not appended; counted in the next round's shortfall

            remaining = count - len(created)

        return created

    return generate_fn
