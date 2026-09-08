"""Section 3: HTTP-layer tests via FastAPI's TestClient — proves routing,
Pydantic request/response shaping (including the `class` field alias),
auth enforcement, and dependency wiring all work together. Business logic
itself is covered by test_quiz_service.py/test_catalog.py/test_auth.py;
these tests would catch a wiring mistake those unit tests can't see (wrong
path, wrong status code, alias/model mismatch)."""

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

from app.auth import get_current_user_id
from app.deps import get_db, get_embed_model, get_fallback_provider, get_primary_provider
from app.main import app

CLASS = "10"
SUBJECT = "Science"


class FakeProvider:
    def generate(self, prompt: str, *, max_tokens: int = 1024) -> str:
        marker = uuid.uuid4().hex
        if "multiple-choice" in prompt:
            return json.dumps({"question": f"fake mcq {marker}?", "options": ["a", "b", "c", "d"], "correct_option_index": 0})
        raise AssertionError("unexpected prompt in API test")


class FakeExplanationProvider:
    def generate(self, prompt: str, *, max_tokens: int = 1024) -> str:
        return "fake explanation"


@pytest.fixture
def client(db_conn, embed_model):
    app.dependency_overrides[get_current_user_id] = lambda: "api_test_user"
    app.dependency_overrides[get_db] = lambda: (yield db_conn)
    app.dependency_overrides[get_embed_model] = lambda: embed_model
    app.dependency_overrides[get_primary_provider] = lambda: FakeProvider()
    app.dependency_overrides[get_fallback_provider] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_health_check_hits_real_db():
    resp = TestClient(app).get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "db": True}


def test_protected_route_rejects_missing_auth():
    resp = TestClient(app).get("/api/profile")
    assert resp.status_code == 401


def test_profile_round_trip(client):
    resp = client.get("/api/profile")
    assert resp.status_code == 200
    assert resp.json()["default_class"] is None

    resp = client.put("/api/profile", json={"default_class": "8"})
    assert resp.status_code == 200
    assert resp.json()["default_class"] == "8"

    resp = client.get("/api/profile")
    assert resp.json()["default_class"] == "8"


def test_catalog_subjects_chapters_topics(client):
    resp = client.get("/api/catalog/subjects", params={"class_": CLASS})
    assert resp.status_code == 200
    assert "Science" in resp.json()

    resp = client.get("/api/catalog/chapters", params={"class_": CLASS, "subject": SUBJECT})
    assert resp.status_code == 200
    chapters = resp.json()
    assert len(chapters) > 1
    chapter_name = chapters[0]["chapter"]

    resp = client.get("/api/catalog/topics", params={"class_": CLASS, "subject": SUBJECT, "chapter": chapter_name})
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


def test_catalog_question_count_options_enforces_coverage_rule(client):
    resp = client.get("/api/catalog/question-count-options", params={"sub_unit_count": 6, "coverage_required": True})
    assert resp.json()["options"] == [10, 15, 20, 25]

    resp = client.get("/api/catalog/question-count-options", params={"sub_unit_count": 6, "coverage_required": False})
    assert resp.json()["options"] == [5, 10, 15, 20, 25]


def test_quiz_create_submit_solutions_round_trip(client):
    chapters = client.get("/api/catalog/chapters", params={"class_": CLASS, "subject": SUBJECT}).json()
    chapter = chapters[0]["chapter"]
    topics = client.get("/api/catalog/topics", params={"class_": CLASS, "subject": SUBJECT, "chapter": chapter}).json()
    topic = topics[0]["topic"]

    create_resp = client.post(
        "/api/quiz/create",
        json={
            "class": CLASS, "subject": SUBJECT, "scope_type": "single_topic", "chapters": [chapter],
            "topics": [topic], "difficulty": "easy", "question_count": 5, "question_types": ["MCQ"],
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    body = create_resp.json()
    assert body["delivered_count"] == 5
    assert all("correct_option_index" not in q["payload"] for q in body["questions"]), "correct answer must never be sent before submission"

    answers = {q["question_id"]: 0 for q in body["questions"]}
    submit_resp = client.post(
        "/api/quiz/submit",
        json={
            "class": CLASS, "subject": SUBJECT, "scope": {"scope_type": "single_topic", "sub_units": [topic]},
            "difficulty": "easy", "is_requiz": False, "answers": answers,
        },
    )
    assert submit_resp.status_code == 200, submit_resp.text
    submit_body = submit_resp.json()
    assert submit_body["score_total"] == 5
    assert submit_body["attempt_id"] is not None
    assert "correct_payload" not in json.dumps(submit_body), "submit response must not leak correct answers (SPEC 8a)"

    app.dependency_overrides[get_primary_provider] = lambda: FakeExplanationProvider()
    sol_resp = client.get(f"/api/quiz/solutions/{submit_body['attempt_id']}")
    assert sol_resp.status_code == 200, sol_resp.text
    solutions = sol_resp.json()["solutions"]
    assert len(solutions) == 5
    assert all(s["explanation"] == "fake explanation" for s in solutions)
    assert all("correct_payload" in s for s in solutions)


def test_quiz_solutions_by_attempt_rejects_other_users_attempt(client):
    chapters = client.get("/api/catalog/chapters", params={"class_": CLASS, "subject": SUBJECT}).json()
    chapter = chapters[0]["chapter"]
    topics = client.get("/api/catalog/topics", params={"class_": CLASS, "subject": SUBJECT, "chapter": chapter}).json()
    topic = topics[0]["topic"]

    create_resp = client.post(
        "/api/quiz/create",
        json={
            "class": CLASS, "subject": SUBJECT, "scope_type": "single_topic", "chapters": [chapter],
            "topics": [topic], "difficulty": "easy", "question_count": 5, "question_types": ["MCQ"],
        },
    )
    answers = {q["question_id"]: 0 for q in create_resp.json()["questions"]}
    submit_resp = client.post(
        "/api/quiz/submit",
        json={
            "class": CLASS, "subject": SUBJECT, "scope": {"scope_type": "single_topic", "sub_units": [topic]},
            "difficulty": "easy", "is_requiz": False, "answers": answers,
        },
    )
    attempt_id = submit_resp.json()["attempt_id"]

    app.dependency_overrides[get_current_user_id] = lambda: "a_different_user"
    resp = client.get(f"/api/quiz/solutions/{attempt_id}")
    assert resp.status_code == 403


def test_requiz_submission_via_api_does_not_return_attempt_id(client):
    chapters = client.get("/api/catalog/chapters", params={"class_": CLASS, "subject": SUBJECT}).json()
    chapter = chapters[0]["chapter"]
    topics = client.get("/api/catalog/topics", params={"class_": CLASS, "subject": SUBJECT, "chapter": chapter}).json()
    topic = topics[0]["topic"]

    create_resp = client.post(
        "/api/quiz/create",
        json={
            "class": CLASS, "subject": SUBJECT, "scope_type": "single_topic", "chapters": [chapter],
            "topics": [topic], "difficulty": "easy", "question_count": 5, "question_types": ["MCQ"],
        },
    )
    answers = {q["question_id"]: 0 for q in create_resp.json()["questions"]}
    submit_resp = client.post(
        "/api/quiz/submit",
        json={
            "class": CLASS, "subject": SUBJECT, "scope": {"scope_type": "single_topic", "sub_units": [topic]},
            "difficulty": "easy", "is_requiz": True, "answers": answers,
        },
    )
    assert submit_resp.json()["attempt_id"] is None


def test_quiz_usage_endpoint(client):
    resp = client.get("/api/quiz/usage")
    assert resp.status_code == 200
    body = resp.json()
    assert body["used_today"] == 0
    assert body["limit"] > 0


def test_daily_limit_returns_429(client, monkeypatch):
    from app.quiz_service import DailyLimitExceeded

    def fake_check(conn, user_id):
        raise DailyLimitExceeded("limit reached")

    monkeypatch.setattr("app.routers.quiz.check_and_log_daily_limit", fake_check)
    resp = client.post(
        "/api/quiz/create",
        json={
            "class": CLASS, "subject": SUBJECT, "scope_type": "single_topic", "chapters": ["whatever"],
            "topics": ["whatever"], "difficulty": "easy", "question_count": 5, "question_types": ["MCQ"],
        },
    )
    assert resp.status_code == 429


def test_dashboard_endpoint_after_an_attempt(client):
    chapters = client.get("/api/catalog/chapters", params={"class_": CLASS, "subject": SUBJECT}).json()
    chapter = chapters[0]["chapter"]
    topics = client.get("/api/catalog/topics", params={"class_": CLASS, "subject": SUBJECT, "chapter": chapter}).json()
    topic = topics[0]["topic"]

    create_resp = client.post(
        "/api/quiz/create",
        json={
            "class": CLASS, "subject": SUBJECT, "scope_type": "single_topic", "chapters": [chapter],
            "topics": [topic], "difficulty": "easy", "question_count": 5, "question_types": ["MCQ"],
        },
    )
    answers = {q["question_id"]: 0 for q in create_resp.json()["questions"]}
    client.post(
        "/api/quiz/submit",
        json={
            "class": CLASS, "subject": SUBJECT, "scope": {"scope_type": "single_topic", "sub_units": [topic]},
            "difficulty": "easy", "is_requiz": False, "answers": answers,
        },
    )

    resp = client.get("/api/performance/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    assert data["overall"]["total"] == 5
    assert any(b["label"] == SUBJECT for b in data["by_subject"])
