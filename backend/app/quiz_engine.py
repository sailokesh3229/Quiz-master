"""Section 2: Steps A-D wired together (task 11) — resolve scope, look up
the bank, generate the shortfall, assemble the delivered quiz.

Scope note: this wiring assumes a single chapter per request (every
sub-unit in QuizRequest.sub_units belongs to it) — sufficient for task
11's "one topic, one difficulty, one question type" wiring test. A
request spanning multiple chapters (SPEC section 5 step 6b/6c allows
picking topics across several selected chapters) would need each
sub-unit paired with its own chapter; that's a real gap to close before
this is genuinely production-ready, flagged here rather than silently
assumed away.

generate_fn: Callable[[AllocationLine, remaining_count], list[dict]] —
injected so this can be tested (task 11) with a mock generator before
real generation is unblocked, and swapped for the real
prompts.py + llm_client.py + dedup.py pipeline once it is.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import psycopg

from app.bank_lookup import lookup_bank
from app.scope_resolution import AllocationLine, QuizRequest, resolve_scope


@dataclass
class AssembledQuestion:
    question_id: str
    question_type: str
    topic: str
    difficulty: str
    source: str  # "bank" | "generated"


def mark_seen(conn: psycopg.Connection, user_id: str, question_ids: list[str]) -> None:
    if not question_ids:
        return
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO seen_questions (user_id, question_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            [(user_id, qid) for qid in question_ids],
        )


def assemble_quiz(
    conn: psycopg.Connection,
    request: QuizRequest,
    chapter: str,
    user_id: str,
    performance_history: dict[tuple[str, str], tuple[int, int]] | None = None,
    generate_fn: Callable[[AllocationLine, int], list[dict]] | None = None,
) -> list[AssembledQuestion]:
    """Steps A-D: resolve scope, look up bank per allocation line
    (excluding clusters already used elsewhere in this quiz — task 11a),
    generate any shortfall via generate_fn, mark everything delivered as
    seen. Raises if a shortfall exists and no generate_fn was provided —
    SPEC section 12: the user always receives exactly the requested count."""
    allocations = resolve_scope(request, performance_history=performance_history)

    assembled: list[AssembledQuestion] = []
    used_cluster_ids: list[str] = []

    for line in allocations:
        bank_result = lookup_bank(
            conn,
            class_=request.class_,
            subject=request.subject,
            chapter=chapter,
            topic=line.sub_unit,
            difficulty=line.difficulty,
            question_type=line.question_type,
            user_id=user_id,
            count=line.count,
            exclude_cluster_ids=used_cluster_ids,
        )
        used_cluster_ids.extend(bank_result.cluster_ids)
        for qid in bank_result.question_ids:
            assembled.append(AssembledQuestion(qid, line.question_type, line.sub_unit, line.difficulty, "bank"))

        if bank_result.shortfall > 0:
            if generate_fn is None:
                raise RuntimeError(
                    f"shortfall of {bank_result.shortfall} for {line.sub_unit}/{line.question_type} "
                    "but no generate_fn provided — SPEC section 12 requires the exact requested count"
                )
            generated = generate_fn(line, bank_result.shortfall)
            for g in generated:
                assembled.append(
                    AssembledQuestion(g["question_id"], line.question_type, line.sub_unit, line.difficulty, "generated")
                )
            if len(generated) < bank_result.shortfall:
                # SPEC section 12: exact requested count must always be
                # delivered — generate_fn is expected to internally retry/
                # top-up (task 14 finding), so coming up short here means
                # the topic is genuinely content-starved (or persistently
                # failing) rather than a transient blip. Surface it clearly
                # instead of silently shipping a short quiz.
                raise RuntimeError(
                    f"generate_fn returned {len(generated)}/{bank_result.shortfall} for "
                    f"{line.sub_unit}/{line.question_type} — SPEC section 12 requires the exact requested count"
                )

    mark_seen(conn, user_id, [a.question_id for a in assembled])
    return assembled
