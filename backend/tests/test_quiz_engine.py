"""Task 11: wire Steps A-D together end-to-end for one topic, one
difficulty, one question type, against a test user with partial prior
history (some seen questions to exclude)."""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.dedup import compute_text_hash, intake_question
from app.quiz_engine import assemble_quiz
from app.scope_resolution import QuizRequest

CHAPTER = "Chemical Reactions and Equations"
TOPIC = "Chemical Equations"


def _seed_bank_question(conn, text, cluster_id=None):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO questions (class, subject, chapter, topic, difficulty, type, text, payload,
                                    source_chunk_id, text_hash, concept_cluster_id)
            VALUES ('10', 'Science', %s, %s, 'medium', 'MCQ', %s, '{}'::jsonb, 'chunk-1', %s, %s)
            RETURNING question_id
            """,
            (CHAPTER, TOPIC, text, compute_text_hash(text), cluster_id or uuid.uuid4()),
        )
        return str(cur.fetchone()["question_id"])


def _mark_seen(conn, user_id, question_id):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO seen_questions (user_id, question_id) VALUES (%s, %s)", (user_id, question_id))


def test_bank_fully_covers_request_no_generation_needed(db_conn):
    # SPEC's fixed question-count options are 5/10/15/20/25 -- seed 5
    ids = [_seed_bank_question(db_conn, f"q{i} text") for i in range(5)]

    request = QuizRequest(
        class_="10", subject="Science", scope_type="single_topic",
        sub_units=[TOPIC], difficulty="medium", question_count=5, question_types=["MCQ"],
    )

    def generate_fn_should_never_be_called(line, count):
        raise AssertionError("generate_fn must not be called when the bank fully covers the request")

    result = assemble_quiz(db_conn, request, chapter=CHAPTER, user_id="new_user",
                            generate_fn=generate_fn_should_never_be_called)

    assert len(result) == 5
    assert {r.question_id for r in result} == set(ids)
    assert all(r.source == "bank" for r in result)


def test_partial_history_excludes_seen_questions_and_generates_shortfall(db_conn):
    # user has already seen 3 of the 4 bank questions -- only 1 is
    # available for a count-5 request, so 4 more must come from generation
    seen_ids = [_seed_bank_question(db_conn, f"seen question {i}") for i in range(3)]
    unseen_id = _seed_bank_question(db_conn, "unseen question")

    user_id = "returning_user"
    for qid in seen_ids:
        _mark_seen(db_conn, user_id, qid)

    request = QuizRequest(
        class_="10", subject="Science", scope_type="single_topic",
        sub_units=[TOPIC], difficulty="medium", question_count=5, question_types=["MCQ"],
    )

    generated_calls = []

    def mock_generate_fn(line, count):
        generated_calls.append((line.sub_unit, line.question_type, count))
        created = []
        for i in range(count):
            text = f"generated question {i} for {line.sub_unit}"
            intake_result = intake_question(
                db_conn, class_="10", subject="Science", chapter=CHAPTER, topic=line.sub_unit,
                difficulty=line.difficulty, question_type=line.question_type, text=text, payload={},
                source_chunk_id="chunk-1", embedding=[0.1] * 384,
            )
            created.append({"question_id": intake_result.question_id})
        return created

    result = assemble_quiz(db_conn, request, chapter=CHAPTER, user_id=user_id, generate_fn=mock_generate_fn)

    assert len(result) == 5, "SPEC section 12: exact requested count must always be delivered"
    result_ids = {r.question_id for r in result}
    for qid in seen_ids:
        assert qid not in result_ids, "already-seen question must be excluded"
    assert unseen_id in result_ids

    bank_sourced = [r for r in result if r.source == "bank"]
    generated_sourced = [r for r in result if r.source == "generated"]
    assert len(bank_sourced) == 1
    assert len(generated_sourced) == 4
    assert generated_calls == [(TOPIC, "MCQ", 4)]


def test_delivered_questions_are_marked_seen_for_next_time(db_conn):
    ids = [_seed_bank_question(db_conn, f"q{i}") for i in range(5)]
    request = QuizRequest(
        class_="10", subject="Science", scope_type="single_topic",
        sub_units=[TOPIC], difficulty="medium", question_count=5, question_types=["MCQ"],
    )
    user_id = "tracked_user"
    assemble_quiz(db_conn, request, chapter=CHAPTER, user_id=user_id)

    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM seen_questions WHERE user_id = %s", (user_id,))
        assert cur.fetchone()["n"] == 5

    # a second identical request for the same user now has NO bank
    # coverage left (all 5 already seen) -- confirms seen-exclusion
    # actually drives generation on a retake, not just on first request
    request2 = QuizRequest(
        class_="10", subject="Science", scope_type="single_topic",
        sub_units=[TOPIC], difficulty="medium", question_count=5, question_types=["MCQ"],
    )
    generated = []

    def gen(line, count):
        generated.append(count)
        # SPEC section 12: generate_fn must fully satisfy the shortfall
        # (assemble_quiz now enforces this, task 14 finding) -- return one
        # freshly-intake'd stand-in per requested question rather than [].
        created = []
        for i in range(count):
            text = f"retake-generated q{i}"
            result = intake_question(
                db_conn, class_="10", subject="Science", chapter=CHAPTER, topic=line.sub_unit,
                difficulty=line.difficulty, question_type=line.question_type, text=text, payload={},
                source_chunk_id="chunk-1", embedding=[0.1] * 384,
            )
            created.append({"question_id": result.question_id})
        return created

    assemble_quiz(db_conn, request2, chapter=CHAPTER, user_id=user_id, generate_fn=gen)
    assert generated == [5], "all 5 now-seen questions must trigger a full shortfall on retake"


def test_no_generator_and_shortfall_raises(db_conn):
    request = QuizRequest(
        class_="10", subject="Science", scope_type="single_topic",
        sub_units=[TOPIC], difficulty="medium", question_count=5, question_types=["MCQ"],
    )
    try:
        assemble_quiz(db_conn, request, chapter=CHAPTER, user_id="u", generate_fn=None)
        assert False, "expected RuntimeError for unfilled shortfall with no generator"
    except RuntimeError as e:
        assert "shortfall" in str(e)
