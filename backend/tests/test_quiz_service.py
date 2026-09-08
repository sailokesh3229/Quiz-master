"""Section 3: quiz_service tests. Bank-seeding tests use dedup.intake_question
directly against real Section 1 topic names (so catalog validation passes)
with placeholder source_chunk_ids. The multi-chapter generation test uses a
FakeProvider (no live LLM cost) but real chunk retrieval, to prove the
per-line chapter routing (PLAN.md section 10) actually works end-to-end.
"""

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.catalog import list_chapters, list_topics
from app.dedup import intake_question
from app.quiz_service import (
    CreateQuizRequest,
    DailyLimitExceeded,
    QuizCreationError,
    SubmitQuizRequest,
    check_and_log_daily_limit,
    create_quiz,
    solutions_from_answers,
    solutions_from_attempt,
    submit_quiz_request,
)

CLASS = "10"
SUBJECT = "Science"
CHAPTER = "Chemical Reactions and Equations"


class FakeProvider:
    """Deterministic, cost-free stand-in for an LLM provider — used only
    to exercise quiz_service's orchestration (chapter routing, shortfall
    handling), not Section 2's already-tested generation/dedup internals."""

    def __init__(self):
        self.calls = 0

    def generate(self, prompt: str, *, max_tokens: int = 1024) -> str:
        self.calls += 1
        marker = uuid.uuid4().hex
        if "multiple-choice" in prompt:
            return json.dumps({"question": f"fake mcq {marker}?", "options": ["a", "b", "c", "d"], "correct_option_index": 0})
        if "fill-in-the-blank" in prompt:
            return json.dumps({"question": f"fake blank {marker} ____", "answer": "term"})
        if "matching question" in prompt:
            return json.dumps(
                {
                    "left_items": [f"l1-{marker}", f"l2-{marker}", f"l3-{marker}"],
                    "right_items": [f"r1-{marker}", f"r2-{marker}", f"r3-{marker}"],
                    "pairs": {f"l1-{marker}": f"r1-{marker}", f"l2-{marker}": f"r2-{marker}", f"l3-{marker}": f"r3-{marker}"},
                }
            )
        raise AssertionError(f"unrecognized prompt shape: {prompt[:80]!r}")


@pytest.fixture
def two_real_chapters():
    chapters = list_chapters(CLASS, SUBJECT)
    assert len(chapters) >= 2
    ch_a, ch_b = chapters[0].chapter, chapters[1].chapter
    topic_a = list_topics(CLASS, SUBJECT, ch_a)[0].topic
    topic_b = list_topics(CLASS, SUBJECT, ch_b)[0].topic
    return ch_a, topic_a, ch_b, topic_b


def _seed_mcq(conn, embed_model, chapter, topic, difficulty="easy", text=None):
    text = text or f"seeded question {uuid.uuid4().hex}"
    payload = {"question": text, "options": ["a", "b", "c", "d"], "correct_option_index": 0}
    embedding = embed_model.encode(text).tolist()
    result = intake_question(
        conn, class_=CLASS, subject=SUBJECT, chapter=chapter, topic=topic, difficulty=difficulty,
        question_type="MCQ", text=text, payload=payload, source_chunk_id="test_chunk", embedding=embedding,
    )
    assert result.accepted
    return result.question_id


_DISTINCT_SEED_TEXTS = [
    "What is the color of copper sulfate solution before the reaction?",
    "Which gas is evolved when zinc reacts with dilute sulfuric acid?",
    "What type of reaction occurs when a precipitate is formed?",
    "Which metal displaces iron from iron sulfate solution?",
    "What happens to the temperature of the surroundings in an exothermic reaction?",
]


def test_create_quiz_served_entirely_from_bank_needs_no_generation(db_conn, embed_model, two_real_chapters):
    ch_a, topic_a, _ch_b, _topic_b = two_real_chapters
    for text in _DISTINCT_SEED_TEXTS:
        _seed_mcq(db_conn, embed_model, ch_a, topic_a, text=text)

    provider_that_must_not_be_called = FakeProvider()
    request = CreateQuizRequest(
        user_id="svc_user_1", class_=CLASS, subject=SUBJECT, scope_type="single_topic",
        chapters=[ch_a], topics=[topic_a], difficulty="easy", question_count=5, question_types=["MCQ"],
    )
    result = create_quiz(db_conn, request, provider_that_must_not_be_called, None, embed_model)

    assert len(result.questions) == 5
    assert all(q.source == "bank" for q in result.questions)
    assert provider_that_must_not_be_called.calls == 0
    assert not result.partial


def test_create_quiz_multi_chapter_routes_generation_to_the_right_chapter(db_conn, embed_model, two_real_chapters):
    ch_a, topic_a, ch_b, topic_b = two_real_chapters
    provider = FakeProvider()

    request = CreateQuizRequest(
        user_id="svc_user_2", class_=CLASS, subject=SUBJECT, scope_type="multi_topic",
        chapters=[ch_a, ch_b], topics=[topic_a, topic_b], difficulty="easy", question_count=10, question_types=["MCQ"],
    )
    result = create_quiz(db_conn, request, provider, None, embed_model)

    assert len(result.questions) == 10
    assert all(q.source == "generated" for q in result.questions)
    by_topic_chapter = {q.topic: q.chapter for q in result.questions}
    assert by_topic_chapter[topic_a] == ch_a
    assert by_topic_chapter[topic_b] == ch_b

    with db_conn.cursor() as cur:
        cur.execute("SELECT topic, chapter FROM questions WHERE question_id = ANY(%s)", ([q.question_id for q in result.questions],))
        for row in cur.fetchall():
            assert (row["topic"] == topic_a and row["chapter"] == ch_a) or (row["topic"] == topic_b and row["chapter"] == ch_b)


def test_create_quiz_allow_partial_skips_generation_on_shortfall(db_conn, embed_model, two_real_chapters):
    ch_a, topic_a, _ch_b, _topic_b = two_real_chapters
    _seed_mcq(db_conn, embed_model, ch_a, topic_a)  # only 1 available

    provider_that_must_not_be_called = FakeProvider()
    request = CreateQuizRequest(
        user_id="svc_user_3", class_=CLASS, subject=SUBJECT, scope_type="single_topic",
        chapters=[ch_a], topics=[topic_a], difficulty="easy", question_count=5, question_types=["MCQ"],
        allow_partial=True,
    )
    result = create_quiz(db_conn, request, provider_that_must_not_be_called, None, embed_model)

    assert result.partial is True
    assert len(result.questions) == 1
    assert provider_that_must_not_be_called.calls == 0


def test_create_quiz_rejects_unknown_chapter(db_conn, embed_model):
    request = CreateQuizRequest(
        user_id="svc_user_4", class_=CLASS, subject=SUBJECT, scope_type="single_topic",
        chapters=["Not A Real Chapter"], topics=["whatever"], difficulty="easy", question_count=5, question_types=["MCQ"],
    )
    with pytest.raises(ValueError):
        create_quiz(db_conn, request, FakeProvider(), None, embed_model)


def test_daily_limit_blocks_after_configured_count(db_conn, monkeypatch):
    monkeypatch.setattr("app.quiz_service.DAILY_QUIZ_GENERATION_LIMIT", 2)
    check_and_log_daily_limit(db_conn, "limit_user")
    check_and_log_daily_limit(db_conn, "limit_user")
    with pytest.raises(DailyLimitExceeded):
        check_and_log_daily_limit(db_conn, "limit_user")


def test_daily_limit_is_per_user(db_conn, monkeypatch):
    monkeypatch.setattr("app.quiz_service.DAILY_QUIZ_GENERATION_LIMIT", 1)
    check_and_log_daily_limit(db_conn, "user_a")
    check_and_log_daily_limit(db_conn, "user_b")  # different user, own quota


def test_submit_quiz_records_attempt_and_updates_counters(db_conn, embed_model, two_real_chapters):
    ch_a, topic_a, _ch_b, _topic_b = two_real_chapters
    qid_correct = _seed_mcq(db_conn, embed_model, ch_a, topic_a)
    qid_wrong = _seed_mcq(db_conn, embed_model, ch_a, topic_a)

    request = SubmitQuizRequest(
        user_id="submit_user_1", class_=CLASS, subject=SUBJECT,
        scope={"scope_type": "single_topic", "sub_units": [topic_a]}, difficulty="easy", is_requiz=False,
        answers={qid_correct: 0, qid_wrong: 1},
    )
    result = submit_quiz_request(db_conn, request, embed_model)

    assert result.attempt_id is not None
    assert result.score_correct == 1
    assert result.score_total == 2
    assert {p["question_id"]: p["is_correct"] for p in result.per_question} == {qid_correct: True, qid_wrong: False}

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT correct_count, total_count FROM performance_counters "
            "WHERE user_id=%s AND class=%s AND subject=%s AND chapter=%s AND topic=%s AND question_type='MCQ'",
            ("submit_user_1", CLASS, SUBJECT, ch_a, topic_a),
        )
        row = cur.fetchone()
        assert (row["correct_count"], row["total_count"]) == (1, 2)


def test_requiz_submission_does_not_record_history(db_conn, embed_model, two_real_chapters):
    ch_a, topic_a, _ch_b, _topic_b = two_real_chapters
    qid = _seed_mcq(db_conn, embed_model, ch_a, topic_a)

    request = SubmitQuizRequest(
        user_id="requiz_user", class_=CLASS, subject=SUBJECT,
        scope={"scope_type": "single_topic", "sub_units": [topic_a]}, difficulty="easy", is_requiz=True,
        answers={qid: 0},
    )
    result = submit_quiz_request(db_conn, request, embed_model)

    assert result.attempt_id is None
    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM performance_counters WHERE user_id=%s", ("requiz_user",))
        assert cur.fetchone()["n"] == 0


def test_solutions_from_attempt_includes_explanation_and_is_cached(db_conn, embed_model, two_real_chapters):
    ch_a, topic_a, _ch_b, _topic_b = two_real_chapters
    qid = _seed_mcq(db_conn, embed_model, ch_a, topic_a)
    submit_result = submit_quiz_request(
        db_conn,
        SubmitQuizRequest(
            user_id="sol_user_1", class_=CLASS, subject=SUBJECT,
            scope={"scope_type": "single_topic", "sub_units": [topic_a]}, difficulty="easy", is_requiz=False,
            answers={qid: 1},  # wrong on purpose
        ),
        embed_model,
    )

    explain_provider = FakeExplanationProvider()
    entries = solutions_from_attempt(db_conn, submit_result.attempt_id, explain_provider, None)
    assert len(entries) == 1
    assert entries[0]["is_correct"] is False
    assert entries[0]["explanation"] == "fake explanation text"
    assert explain_provider.calls == 1

    entries_again = solutions_from_attempt(db_conn, submit_result.attempt_id, explain_provider, None)
    assert entries_again[0]["explanation"] == "fake explanation text"
    assert explain_provider.calls == 1, "explanation should be cached on the questions row, not regenerated"


def test_solutions_from_answers_works_without_a_stored_attempt(db_conn, embed_model, two_real_chapters):
    ch_a, topic_a, _ch_b, _topic_b = two_real_chapters
    qid = _seed_mcq(db_conn, embed_model, ch_a, topic_a)
    entries = solutions_from_answers(db_conn, {qid: 0}, FakeExplanationProvider(), None, embed_model)
    assert entries[0]["is_correct"] is True
    assert entries[0]["explanation"] == "fake explanation text"


class FakeExplanationProvider:
    def __init__(self):
        self.calls = 0

    def generate(self, prompt: str, *, max_tokens: int = 1024) -> str:
        self.calls += 1
        return "fake explanation text"
