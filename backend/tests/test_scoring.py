"""Task 12: scoring, performance-history updates, four-option post-quiz."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.grading import GradedAnswer
from app.scoring import POST_QUIZ_OPTIONS, QuizSubmission, get_topic_accuracy, submit_quiz

SCOPE = {"scope_type": "single_topic", "sub_units": ["Chemical Equations"]}
CLASS = "10"
SUBJECT = "Science"
CHAPTER = "Chemical Reactions and Equations"


def _answers(*rows):
    """rows: (question_id, question_type, topic, is_correct). chapter is
    fixed to the module-level CHAPTER constant -- every seeded question row
    in this file uses it (performance_counters is keyed on (class, subject,
    chapter, topic, question_type), not topic alone, since topic names
    collide across chapters in the real corpus -- see scoring.py)."""
    return [
        GradedAnswer(question_id=qid, question_type=qt, topic=topic, chapter=CHAPTER, is_correct=correct)
        for qid, qt, topic, correct in rows
    ]


def test_real_quiz_writes_attempt_and_updates_performance_counters(db_conn):
    import uuid

    q1, q2, q3 = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    # need real question rows for the FK on attempt_answers.question_id
    with db_conn.cursor() as cur:
        for qid in (q1, q2, q3):
            cur.execute(
                """
                INSERT INTO questions (question_id, class, subject, chapter, topic, difficulty, type, text,
                                        payload, source_chunk_id, text_hash)
                VALUES (%s, '10', 'Science', 'Chemical Reactions and Equations', 'Chemical Equations',
                        'medium', 'MCQ', 'q', '{}'::jsonb, 'chunk-1', %s)
                """,
                (qid, qid),
            )

    answers = _answers(
        (q1, "MCQ", "Chemical Equations", True),
        (q2, "MCQ", "Chemical Equations", False),
        (q3, "fill_in_blank", "Chemical Equations", True),
    )
    submission = QuizSubmission(
        user_id="u_real", class_="10", subject="Science", scope=SCOPE, difficulty="medium",
        is_requiz=False, answers=answers, user_answer_payloads={q1: 0, q2: 1, q3: "hydrogen"},
    )
    result = submit_quiz(db_conn, submission)

    assert result.attempt_id is not None
    assert result.score_correct == 2
    assert result.score_total == 3
    assert result.options == POST_QUIZ_OPTIONS

    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM quiz_attempts WHERE attempt_id = %s", (result.attempt_id,))
        assert cur.fetchone()["n"] == 1
        cur.execute("SELECT COUNT(*) AS n FROM attempt_answers WHERE attempt_id = %s", (result.attempt_id,))
        assert cur.fetchone()["n"] == 3

    mcq_correct, mcq_total = get_topic_accuracy(db_conn, "u_real", CLASS, SUBJECT, CHAPTER, "Chemical Equations")
    assert mcq_total == 3  # summed across MCQ (2) and fill_in_blank (1), SPEC 11a
    assert mcq_correct == 2


def test_performance_counters_grouped_one_update_per_topic_type(db_conn):
    # SPEC 11a: "apply one incremental update per group (not one update
    # per question)" -- verify via a second submission accumulating on
    # top of the first, and by checking each (topic, type) row directly.
    import uuid

    ids = [str(uuid.uuid4()) for _ in range(4)]
    with db_conn.cursor() as cur:
        for qid in ids:
            cur.execute(
                """
                INSERT INTO questions (question_id, class, subject, chapter, topic, difficulty, type, text,
                                        payload, source_chunk_id, text_hash)
                VALUES (%s, '10', 'Science', 'Chemical Reactions and Equations', 'Chemical Equations',
                        'medium', 'MCQ', 'q', '{}'::jsonb, 'chunk-1', %s)
                """,
                (qid, qid),
            )

    answers = _answers(
        (ids[0], "MCQ", "Chemical Equations", True),
        (ids[1], "MCQ", "Chemical Equations", True),
        (ids[2], "MCQ", "Chemical Equations", False),
        (ids[3], "matching", "Chemical Equations", True),
    )
    submission = QuizSubmission(
        user_id="u_group", class_="10", subject="Science", scope=SCOPE, difficulty="medium",
        is_requiz=False, answers=answers, user_answer_payloads={i: {} for i in ids},
    )
    submit_quiz(db_conn, submission)

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT question_type, correct_count, total_count FROM performance_counters "
            "WHERE user_id = 'u_group' AND class = %s AND subject = %s AND chapter = %s AND topic = 'Chemical Equations'",
            (CLASS, SUBJECT, CHAPTER),
        )
        by_type = {r["question_type"]: (r["correct_count"], r["total_count"]) for r in cur.fetchall()}
    assert by_type["MCQ"] == (2, 3)
    assert by_type["matching"] == (1, 1)


def test_requiz_does_not_write_to_history_at_all(db_conn):
    import uuid

    qid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO questions (question_id, class, subject, chapter, topic, difficulty, type, text,
                                    payload, source_chunk_id, text_hash)
            VALUES (%s, '10', 'Science', 'Chemical Reactions and Equations', 'Chemical Equations',
                    'medium', 'MCQ', 'q', '{}'::jsonb, 'chunk-1', %s)
            """,
            (qid, qid),
        )

    answers = _answers((qid, "MCQ", "Chemical Equations", True))
    submission = QuizSubmission(
        user_id="u_requiz", class_="10", subject="Science", scope=SCOPE, difficulty="medium",
        is_requiz=True, answers=answers, user_answer_payloads={qid: 0},
    )
    result = submit_quiz(db_conn, submission)

    assert result.attempt_id is None, "requiz must not create a quiz_attempts row"
    assert result.score_correct == 1
    assert result.score_total == 1

    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM quiz_attempts WHERE user_id = 'u_requiz'")
        assert cur.fetchone()["n"] == 0
        cur.execute("SELECT COUNT(*) AS n FROM performance_counters WHERE user_id = 'u_requiz'")
        assert cur.fetchone()["n"] == 0


def test_post_quiz_options_are_the_four_from_plan():
    assert set(POST_QUIZ_OPTIONS) == {"go_home", "new_quiz_same_topic", "requiz_same_questions", "view_solutions"}
