"""Section 2, Step E: score, record, and offer next steps.

On submission: grade (grading.py), compute overall score, update
performance-history counters (SPEC 11a) grouped by (chapter, topic,
question_type) — ONE incremental update per group, not per question,
scoped by chapter+topic rather than topic alone since topic names collide
across chapters in the real corpus (see grading.py's GradedAnswer.chapter)
— and present the
four post-quiz options (PLAN.md section 2 Step E): go home, new quiz
(same topic, unseen questions), requiz (replay, no history update), view
solutions. The requiz path skips ALL history-update logic — no
quiz_attempts row, no attempt_answers rows, no performance_counters
update (SPEC section 11: "not recorded to history, does not affect
accuracy").
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import psycopg

from app.grading import GradedAnswer

POST_QUIZ_OPTIONS = ("go_home", "new_quiz_same_topic", "requiz_same_questions", "view_solutions")


@dataclass
class QuizSubmission:
    user_id: str
    class_: str
    subject: str
    scope: dict  # {"scope_type": ..., "sub_units": [...]}
    difficulty: str
    is_requiz: bool
    answers: list[GradedAnswer]
    user_answer_payloads: dict[str, object]  # question_id -> raw user answer, for attempt_answers


@dataclass
class QuizSubmitResult:
    attempt_id: str | None  # None for a requiz — nothing was recorded
    score_correct: int
    score_total: int
    options: tuple[str, ...] = POST_QUIZ_OPTIONS


def _update_performance_counters(
    conn: psycopg.Connection, user_id: str, class_: str, subject: str, answers: list[GradedAnswer]
) -> None:
    """SPEC 11a: group by (chapter, topic, question_type), one incremental
    UPSERT per group — not one per question. Grouped by chapter+topic, not
    topic alone: topic names collide across chapters in the real corpus
    (e.g. "Introduction" is shared by 255 different chapters) — class_/
    subject are fixed for the whole submission (one class+subject per
    quiz, SPEC section 5), only chapter varies across answers."""
    groups: dict[tuple[str, str, str], list[int]] = defaultdict(lambda: [0, 0])  # [correct, total]
    for a in answers:
        g = groups[(a.chapter, a.topic, a.question_type)]
        g[1] += 1
        if a.is_correct:
            g[0] += 1

    with conn.cursor() as cur:
        for (chapter, topic, qtype), (correct, total) in groups.items():
            cur.execute(
                """
                INSERT INTO performance_counters (user_id, class, subject, chapter, topic, question_type, correct_count, total_count)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id, class, subject, chapter, topic, question_type)
                DO UPDATE SET
                    correct_count = performance_counters.correct_count + EXCLUDED.correct_count,
                    total_count = performance_counters.total_count + EXCLUDED.total_count
                """,
                (user_id, class_, subject, chapter, topic, qtype, correct, total),
            )


def submit_quiz(conn: psycopg.Connection, submission: QuizSubmission) -> QuizSubmitResult:
    score_correct = sum(1 for a in submission.answers if a.is_correct)
    score_total = len(submission.answers)

    if submission.is_requiz:
        # SPEC section 11 / PLAN Step E: requiz skips history-update logic
        # entirely — no quiz_attempts row, no attempt_answers rows, no
        # performance_counters change. The score is still computed and
        # returned for immediate display, just never persisted.
        return QuizSubmitResult(attempt_id=None, score_correct=score_correct, score_total=score_total)

    import json

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO quiz_attempts (user_id, class, subject, scope, difficulty, score_correct, score_total)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING attempt_id
            """,
            (submission.user_id, submission.class_, submission.subject, json.dumps(submission.scope),
             submission.difficulty, score_correct, score_total),
        )
        attempt_id = str(cur.fetchone()["attempt_id"])

        for a in submission.answers:
            cur.execute(
                """
                INSERT INTO attempt_answers (attempt_id, question_id, user_answer, is_correct)
                VALUES (%s, %s, %s, %s)
                """,
                (attempt_id, a.question_id, json.dumps(submission.user_answer_payloads[a.question_id]), a.is_correct),
            )

    _update_performance_counters(conn, submission.user_id, submission.class_, submission.subject, submission.answers)

    return QuizSubmitResult(attempt_id=attempt_id, score_correct=score_correct, score_total=score_total)


def get_topic_accuracy(conn: psycopg.Connection, user_id: str, class_: str, subject: str, chapter: str, topic: str) -> tuple[int, int]:
    """Summed across all question types for this (chapter, topic) — SPEC
    11a: higher-level accuracy is summed from leaf (topic, type) counters.
    Scoped by chapter (not topic alone): topic names collide across
    chapters in the real corpus (e.g. "Introduction" appears in 255
    different chapters)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COALESCE(SUM(correct_count), 0) AS correct, COALESCE(SUM(total_count), 0) AS total
            FROM performance_counters WHERE user_id = %s AND class = %s AND subject = %s AND chapter = %s AND topic = %s
            """,
            (user_id, class_, subject, chapter, topic),
        )
        row = cur.fetchone()
        return int(row["correct"]), int(row["total"])
