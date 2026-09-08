"""Section 2, Step B: bank lookup, per allocation line.

A single indexed relational query per (topic, difficulty, type, count)
line: scope + type filter, exclude this user's seen_questions, at most
one question per concept_cluster_id — no embedding comparison at read
time (PLAN.md section 2 Step B / section 4a).

Task 11a: cross-type similarity scope. Bank lookup filters by ONE type
per call (an allocation line is always a single type), so cross-type
cluster exclusion within a single quiz assembly needs the clusters
already used by EARLIER allocation lines in that same quiz passed in via
exclude_cluster_ids — still a plain relational filter, no runtime
embedding comparison. The quiz-assembly loop (task 11) accumulates
BankLookupResult.cluster_ids across calls and threads them through.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import psycopg


@dataclass
class BankLookupResult:
    class_: str
    subject: str
    chapter: str
    topic: str
    difficulty: str
    question_type: str
    requested_count: int
    question_ids: list[str]
    cluster_ids: list[str] = field(default_factory=list)  # parallel to question_ids

    @property
    def shortfall(self) -> int:
        return self.requested_count - len(self.question_ids)


# COALESCE(concept_cluster_id, question_id): a row that hasn't been
# clustered yet (should never happen post-task-9, but defends against it)
# is treated as its own unique cluster rather than colliding with other
# NULLs — DISTINCT ON treats all NULLs as equal, which would otherwise
# silently collapse every not-yet-clustered question in scope to one.
#
# exclude_cluster_ids: != ALL(%(exclude_clusters)s) is vacuously true for
# an empty array, so this is a no-op filter when nothing's excluded yet
# (the first allocation line of a quiz).
_QUERY = """
    SELECT question_id, cluster_id FROM (
        SELECT DISTINCT ON (COALESCE(concept_cluster_id, question_id))
            question_id, COALESCE(concept_cluster_id, question_id) AS cluster_id
        FROM questions q
        WHERE q.class = %(class_)s
          AND q.subject = %(subject)s
          AND q.chapter = %(chapter)s
          AND q.topic = %(topic)s
          AND q.difficulty = %(difficulty)s
          AND q.type = %(question_type)s
          AND NOT EXISTS (
              SELECT 1 FROM seen_questions sq
              WHERE sq.user_id = %(user_id)s AND sq.question_id = q.question_id
          )
          AND COALESCE(concept_cluster_id, question_id) != ALL(%(exclude_clusters)s::uuid[])
        ORDER BY COALESCE(concept_cluster_id, question_id), random()
    ) deduped
    ORDER BY random()
    LIMIT %(count)s
"""


def lookup_bank(
    conn: psycopg.Connection,
    class_: str,
    subject: str,
    chapter: str,
    topic: str,
    difficulty: str,
    question_type: str,
    user_id: str,
    count: int,
    exclude_cluster_ids: list[str] | None = None,
) -> BankLookupResult:
    with conn.cursor() as cur:
        cur.execute(
            _QUERY,
            {
                "class_": class_,
                "subject": subject,
                "chapter": chapter,
                "topic": topic,
                "difficulty": difficulty,
                "question_type": question_type,
                "user_id": user_id,
                "count": count,
                "exclude_clusters": exclude_cluster_ids or [],
            },
        )
        rows = cur.fetchall()
    return BankLookupResult(
        class_=class_,
        subject=subject,
        chapter=chapter,
        topic=topic,
        difficulty=difficulty,
        question_type=question_type,
        requested_count=count,
        question_ids=[str(r["question_id"]) for r in rows],
        cluster_ids=[str(r["cluster_id"]) for r in rows],
    )
