import pytest
from app.answer_aliases import accepted_answers_for
from app.grading import grade_fill_in_blank
from app import explanations, quiz_service
from app.source_quality import repair_source, REPAIRED_CHUNK_IDS, REVIEW_NOTICE
from test_latency_fixes import Connection


@pytest.mark.parametrize("wrong", ["molecules", "particles", "ions"])
def test_related_terms_never_become_equivalent(wrong):
    class Model:
        def encode(self, values):
            pytest.fail("grading must not load or run embeddings")
    payload = {"answer": "atoms", "accepted_answers": [wrong]}
    assert wrong not in accepted_answers_for(payload)
    assert not grade_fill_in_blank(payload, wrong, Model())
    assert grade_fill_in_blank(payload, "atom", Model())


def test_unreviewed_alias_is_ignored_for_existing_bank_payload():
    assert accepted_answers_for({"answer": "evaporation", "accepted_answers": ["boiling"]}) == []


def test_cache_only_never_calls_provider_or_source(monkeypatch):
    conn = Connection([dict(question_id="a", explanation=None), dict(question_id="b", explanation="cached")])
    monkeypatch.setattr(explanations, "_get_chunk_text", lambda *a: pytest.fail("source read"))
    assert explanations.get_explanations(conn, ["a", "b"], None, cache_only=True) == {"a": "", "b": "cached"}
    assert conn.updates == []


def test_old_affected_question_does_not_reuse_bad_explanation():
    conn = Connection([dict(question_id="a", source_chunk_id=next(iter(REPAIRED_CHUNK_IDS)),
                            payload={}, explanation="wrong cached explanation")])
    assert explanations.get_explanations(conn, ["a"], None)["a"] == REVIEW_NOTICE
    assert not conn.updates


def test_source_repair_preserves_subscripts_and_is_idempotent():
    damaged = "3Fe + 4HO → FeO + 4H\n2342"
    fixed = "3Fe + 4H2O → Fe3O4 + 4H2"
    assert repair_source(damaged) == fixed
    assert repair_source(fixed) == fixed
    assert repair_source("FeO is iron(II) oxide") == "FeO is iron(II) oxide"


def test_single_explanation_must_belong_to_attempt(monkeypatch):
    conn = Connection([dict(question_id="owned")])
    monkeypatch.setattr(quiz_service, "get_explanations", lambda *a, **kw: pytest.fail("provider called"))
    with pytest.raises(ValueError, match="belong"):
        quiz_service.solutions_from_attempt(conn, "attempt", None, None, question_id="other")


def test_review_preserves_saved_grade_and_skips_generation(monkeypatch):
    conn = Connection([dict(question_id="owned", type="fill_in_blank", topic="t", text="q",
                            payload={"answer": "atoms", "accepted_answers": ["molecules"]},
                            user_answer="molecules", is_correct=True)])
    def cached(conn, ids, provider, fallback, *, cache_only):
        assert cache_only
        return {"owned": ""}
    monkeypatch.setattr(quiz_service, "get_explanations", cached)
    entries = quiz_service.solutions_from_attempt(conn, "attempt", None, None, include_explanations=False)
    assert entries[0]["is_correct"] is True
    assert "molecules" not in entries[0]["correct_payload"]["accepted_answers"]
    assert entries[0]["explanation"] == ""


def test_prefill_requires_review_and_current_source():
    from app.prefill import validate_candidate, digest
    candidate = dict(approved=False, difficulty="easy", type="fill_in_blank", chunk_id="c",
                     source_sha256=digest("source"), payload={"question": "____", "answer": "atoms"})
    chunks = {"c": {"text": "source"}}
    with pytest.raises(ValueError, match="approved"):
        validate_candidate(candidate, {}, chunks)
    candidate["approved"] = True
    assert validate_candidate(candidate, {}, chunks).answer == "atoms"
    with pytest.raises(ValueError, match="source changed"):
        validate_candidate(candidate, {}, {"c": {"text": "changed"}})


@pytest.mark.parametrize("count", [5, 15, 25])
def test_generation_encodes_one_batch_not_each_question(monkeypatch, count):
    import numpy as np
    from types import SimpleNamespace
    from app import generation
    from app.prompts import FillInBlankPayload
    from app.scope_resolution import AllocationLine
    calls, writes = [], []
    monkeypatch.setattr(generation, "get_wording_style_example", lambda *a: None)
    monkeypatch.setattr(generation, "get_grounding_chunks", lambda *a: [{"text": "source", "chunk_id": "c"}])
    monkeypatch.setattr(generation, "_generate_one_validated_question", lambda *a: {
        "payload": FillInBlankPayload(question="q ____", answer="atoms"), "chunk_id": "c"})
    def encode(texts):
        calls.append(len(texts))
        return np.ones((len(texts), 384))
    def intake(*a, **kw):
        writes.append(kw)
        return SimpleNamespace(accepted=True, question_id=str(len(writes)))
    monkeypatch.setattr(generation, "intake_question", intake)
    fn = generation.make_generate_fn(None, "10", "Science", "chapter", None, None, SimpleNamespace(encode=encode))
    assert len(fn(AllocationLine("topic", "easy", "fill_in_blank", count), count)) == count
    assert calls == [count]
    assert len(writes) == count
