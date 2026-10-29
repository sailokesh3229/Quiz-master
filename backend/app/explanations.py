"""Section 3 (SPEC section 8a, PLAN.md section 3 section 4 'grading
engine'): source-grounded, reference-corpus-styled explanations for the
post-quiz solutions screen. Not built in Section 2 (question generation
never produces an explanation field — see prompts.py's schemas) because
SPEC 8a scopes explanation generation to the solutions screen itself, not
question creation.

Generated lazily, on first solutions-screen view, and cached on
`questions.explanation` (not user-specific — every user who eventually
sees this question gets the same textbook-grounded explanation, so
generating it once and reusing it is correct, not just an optimization).
"""

from __future__ import annotations

import os
from pathlib import Path
from functools import lru_cache

import chromadb
import psycopg

from app.llm_client import Provider, generate_with_reliability
from app.background import generate_parallel
from app.diagnostics import stage, logger

_DEFAULT_CHROMA_PATH = Path(__file__).resolve().parent.parent.parent / "ingestion" / "chroma_db"
CHROMA_PATH = Path(os.environ.get("CHROMA_DB_PATH", str(_DEFAULT_CHROMA_PATH)))


@lru_cache(maxsize=1024)
def _get_chunk_text(chunk_id: str) -> str | None:
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    coll = client.get_collection("chunks")
    res = coll.get(ids=[chunk_id], include=["documents"])
    docs = res["documents"]
    return docs[0] if docs else None


def _get_style_reference(conn: psycopg.Connection, class_: str, subject: str, chapter: str, topic: str) -> dict | None:
    """SPEC 8a: best-effort — use a captured in-text-exercise/CBSE
    official answer for this scope as a style reference, so the
    explanation's structure resembles how NCERT/CBSE present solutions.
    Prefers a same-topic example, falls back to same-chapter."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT question, answer FROM reference_corpus
            WHERE class = %s AND subject = %s AND chapter = %s AND answer IS NOT NULL
            ORDER BY (topic = %s) DESC, random()
            LIMIT 1
            """,
            (class_, subject, chapter, topic),
        )
        return cur.fetchone()


def _correct_answer_text(question_type: str, payload: dict) -> str:
    if question_type == "MCQ":
        return payload["options"][payload["correct_option_index"]]
    if question_type == "fill_in_blank":
        return payload["answer"]
    if question_type == "matching":
        pairs = payload["pairs"]
        return "; ".join(f"{left} -> {right}" for left, right in pairs.items())
    raise ValueError(f"unknown question_type: {question_type}")


def _build_explanation_prompt(question_text: str, question_type: str, payload: dict, chunk_text: str, style_ref: dict | None) -> str:
    correct = _correct_answer_text(question_type, payload)
    style_block = ""
    if style_ref is not None:
        style_block = f"""
Match the structure/style (e.g. stepwise working, point-format reasoning) of this official textbook-style answer to a DIFFERENT question, as a formatting reference only — do not reuse its content:
Q: {style_ref["question"]}
A: {style_ref["answer"]}
"""
    return f"""A student answered this question incorrectly. Write a short, clear explanation of why the correct answer is right, grounded ONLY in the textbook content below.

TEXTBOOK CONTENT:
\"\"\"
{chunk_text}
\"\"\"

QUESTION: {question_text}
CORRECT ANSWER: {correct}
{style_block}
Write 2-4 sentences (or short numbered/point steps if that fits better) explaining why this is correct, referencing the textbook content directly. Do not mention "the textbook" or "the passage" explicitly — write as a direct explanation to the student. Respond with plain text only, no JSON, no markdown headers."""


def get_or_generate_explanation(
    conn: psycopg.Connection,
    question_id: str,
    primary: Provider,
    fallback: Provider | None = None,
) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT text, type, payload, source_chunk_id, class, subject, chapter, topic, explanation
            FROM questions WHERE question_id = %s
            """,
            (question_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise ValueError(f"unknown question_id: {question_id}")
    if row["explanation"]:
        return row["explanation"]

    chunk_text = _get_chunk_text(row["source_chunk_id"]) or ""
    style_ref = _get_style_reference(conn, row["class"], row["subject"], row["chapter"], row["topic"])
    prompt = _build_explanation_prompt(row["text"], row["type"], row["payload"], chunk_text, style_ref)
    explanation = generate_with_reliability(prompt, primary=primary, fallback=fallback, max_tokens=400).strip()

    with conn.cursor() as cur:
        cur.execute("UPDATE questions SET explanation = %s WHERE question_id = %s", (explanation, question_id))
    # No commit here — caching survives only if the caller's transaction
    # commits (request-boundary commit, app/deps.py), same convention as
    # every other write in this codebase (Section 2's dedup.py/scoring.py
    # never commit internally either). Worst case on a request that later
    # fails: the explanation is regenerated next time, not corrupted.
    return explanation


def get_explanations(conn, question_ids, primary, fallback=None) -> dict[str, str]:
    """Bulk cache read; only provider calls run in workers. DB stays on caller."""
    ids = list(dict.fromkeys(question_ids))
    if not ids:
        return {}
    with conn.cursor() as cur:
        cur.execute("SELECT question_id, text, type, payload, source_chunk_id, class, subject, chapter, topic, explanation "
                    "FROM questions WHERE question_id = ANY(%s)", (ids,))
        rows = cur.fetchall()
    if len(rows) != len(ids):
        raise ValueError("unknown question_id in solutions request")
    result = {str(r["question_id"]): r["explanation"] for r in rows if r["explanation"]}
    pending, chunks, styles = [], {}, {}
    for row in rows:
        if row["explanation"]:
            continue
        chunk_id = row["source_chunk_id"]
        if chunk_id not in chunks:
            chunks[chunk_id] = _get_chunk_text(chunk_id) or ""
        scope = (row["class"], row["subject"], row["chapter"], row["topic"])
        if scope not in styles:
            styles[scope] = _get_style_reference(conn, *scope)
        pending.append((str(row["question_id"]), _build_explanation_prompt(
            row["text"], row["type"], row["payload"], chunks[chunk_id], styles[scope])))
    logger.info("solutions cache_hits=%d missing=%d", len(result), len(pending))
    if not pending:
        return result
    with stage("solutions.generate_parallel"):
        outcome = generate_parallel(pending, lambda item: generate_with_reliability(
            item[1], primary=primary, fallback=fallback, max_tokens=400, max_retries=1).strip())
    if outcome.errors:
        raise outcome.errors[min(outcome.errors)]
    updates = []
    for index, explanation in outcome.results.items():
        if not explanation:
            raise ValueError("empty explanation returned by provider")
        qid = pending[index][0]
        result[qid] = explanation
        updates.append((explanation, qid))
    with conn.cursor() as cur:
        cur.executemany("UPDATE questions SET explanation=%s WHERE question_id=%s", updates)
    return result
