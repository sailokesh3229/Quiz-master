"""Section 3: performance dashboard aggregation (SPEC section 11/11a,
PLAN.md section 3 API surface 'Performance' + design screen 11 'My
progress'). Derives overall/by-subject/by-chapter/by-question-type
accuracy from Section 2's `performance_counters` (leaf-level (user, class,
subject, chapter, topic, question_type) counters, summed at read time per
SPEC 11a — nothing is recomputed by scanning raw answer history) plus an
attempts-over-time trend from `quiz_attempts`.

By-subject/by-chapter grouping reads `performance_counters.subject`/
`.chapter` directly (stored per row since the 2026-09-08 schema fix) rather
than reverse-looking-up a topic name against the Section 1 catalog — topic
names are NOT globally unique across the corpus ("Introduction" alone is
shared by 255 different chapters), so a name-based reverse lookup would
misattribute counters to whichever chapter happened to be scanned first.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import psycopg


@dataclass
class AccuracyBucket:
    label: str
    correct: int
    total: int

    @property
    def accuracy(self) -> float | None:
        return None if self.total == 0 else self.correct / self.total


@dataclass
class WeeklyAttempts:
    week_start: str  # ISO date, Monday of that week
    correct: int
    total: int
    attempt_count: int


@dataclass
class DashboardData:
    overall: AccuracyBucket
    by_subject: list[AccuracyBucket] = field(default_factory=list)
    by_chapter: list[AccuracyBucket] = field(default_factory=list)  # label = "Subject / Chapter"
    by_question_type: list[AccuracyBucket] = field(default_factory=list)
    by_difficulty: list[AccuracyBucket] = field(default_factory=list)
    attempts_over_time: list[WeeklyAttempts] = field(default_factory=list)
    recent_attempts: list[dict] = field(default_factory=list)


def _fetch_performance_counters(conn: psycopg.Connection, user_id: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT subject, chapter, topic, question_type, correct_count, total_count "
            "FROM performance_counters WHERE user_id = %s",
            (user_id,),
        )
        return cur.fetchall()


def _fetch_attempts(conn: psycopg.Connection, user_id: str, limit: int = 200) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT attempt_id, class, subject, scope, difficulty, score_correct, score_total, created_at
            FROM quiz_attempts WHERE user_id = %s ORDER BY created_at DESC LIMIT %s
            """,
            (user_id, limit),
        )
        return cur.fetchall()


def _bucket_by_difficulty(conn: psycopg.Connection, user_id: str) -> list[AccuracyBucket]:
    """Difficulty isn't in performance_counters (only topic/question_type),
    so this rolls up from quiz_attempts instead — a coarser, attempt-level
    view (whole-attempt score per difficulty) rather than the per-question
    precision the topic-based buckets get from Section 2's counters."""
    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    with conn.cursor() as cur:
        cur.execute(
            "SELECT difficulty, score_correct, score_total FROM quiz_attempts WHERE user_id = %s",
            (user_id,),
        )
        for row in cur.fetchall():
            bucket = totals[row["difficulty"]]
            bucket[0] += row["score_correct"]
            bucket[1] += row["score_total"]
    return [AccuracyBucket(label=d, correct=c, total=t) for d, (c, t) in sorted(totals.items())]


def _week_start(dt) -> str:
    monday = dt.date() - __import__("datetime").timedelta(days=dt.weekday())
    return monday.isoformat()


def get_dashboard(conn: psycopg.Connection, user_id: str) -> DashboardData:
    counters = _fetch_performance_counters(conn, user_id)

    overall_c = overall_t = 0
    by_subject: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    by_chapter: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    by_type: dict[str, list[int]] = defaultdict(lambda: [0, 0])

    for row in counters:
        c, t = row["correct_count"], row["total_count"]
        overall_c += c
        overall_t += t

        by_type[row["question_type"]][0] += c
        by_type[row["question_type"]][1] += t

        by_subject[row["subject"]][0] += c
        by_subject[row["subject"]][1] += t
        chapter_label = f"{row['subject']} / {row['chapter']}"
        by_chapter[chapter_label][0] += c
        by_chapter[chapter_label][1] += t

    weekly: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    attempts = _fetch_attempts(conn, user_id)
    for a in attempts:
        wk = _week_start(a["created_at"])
        bucket = weekly[wk]
        bucket[0] += a["score_correct"]
        bucket[1] += a["score_total"]
        bucket[2] += 1

    return DashboardData(
        overall=AccuracyBucket("Overall", overall_c, overall_t),
        by_subject=[AccuracyBucket(k, c, t) for k, (c, t) in sorted(by_subject.items())],
        by_chapter=[AccuracyBucket(k, c, t) for k, (c, t) in sorted(by_chapter.items())],
        by_question_type=[AccuracyBucket(k, c, t) for k, (c, t) in sorted(by_type.items())],
        by_difficulty=_bucket_by_difficulty(conn, user_id),
        attempts_over_time=sorted(
            (WeeklyAttempts(wk, c, t, n) for wk, (c, t, n) in weekly.items()), key=lambda w: w.week_start
        ),
        recent_attempts=[
            {
                "attempt_id": str(a["attempt_id"]),
                "class": a["class"],
                "subject": a["subject"],
                "scope": a["scope"],
                "difficulty": a["difficulty"],
                "score_correct": a["score_correct"],
                "score_total": a["score_total"],
                "created_at": a["created_at"].isoformat(),
            }
            for a in attempts[:20]
        ],
    )
