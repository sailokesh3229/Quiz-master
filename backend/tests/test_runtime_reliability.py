"""Regression checks for request-time model loading and multiplied retries."""
import pytest

from app import deps, generation, llm_client
from app.scope_resolution import AllocationLine


def test_model_loading_is_offline_and_cpu_only(monkeypatch):
    calls = []
    from app import embeddings
    monkeypatch.setattr(embeddings, "MiniLMEncoder", lambda: calls.append("local_model") or object())
    deps._embed_model.cache_clear()
    try:
        first = deps.get_embed_model()
        assert deps.get_embed_model() is first
        assert calls == ["local_model"]
    finally:
        deps._embed_model.cache_clear()


def test_provider_client_disables_nested_sdk_retries(monkeypatch):
    calls = []
    monkeypatch.setattr(llm_client.openai, "OpenAI", lambda **kwargs: calls.append(kwargs))
    llm_client.OpenAIProvider(api_key="test-only")
    assert calls[0]["max_retries"] == 0
    assert calls[0]["timeout"] == llm_client.REQUEST_TIMEOUT


def test_missing_fallback_key_does_not_block_primary(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert llm_client.default_fallback() is None


def test_provider_failure_is_not_repeated_in_outer_rounds(monkeypatch):
    calls = []
    monkeypatch.setattr(generation, "get_wording_style_example", lambda *args: None)
    monkeypatch.setattr(generation, "get_grounding_chunks", lambda *args: [{"text": "source", "chunk_id": "one"}])

    def failed(*args):
        calls.append(1)
        raise TimeoutError("provider timed out")

    monkeypatch.setattr(generation, "_generate_one_validated_question", failed)
    generate = generation.make_generate_fn(None, "10", "Science", "chapter", None, None, None)
    with pytest.raises(TimeoutError):
        generate(AllocationLine("topic", "easy", "MCQ", 2), 2)
    assert len(calls) == 2


def test_matching_question_reaches_bank_intake(monkeypatch):
    import json
    import numpy as np
    from types import SimpleNamespace

    monkeypatch.setattr(generation, "get_wording_style_example", lambda *args: None)
    monkeypatch.setattr(generation, "get_grounding_chunks", lambda *args: [{"text": "source", "chunk_id": "one"}])
    accepted = []

    def intake(*args, **kwargs):
        accepted.append(kwargs)
        return SimpleNamespace(accepted=True, question_id="matching-one")

    monkeypatch.setattr(generation, "intake_question", intake)
    provider = SimpleNamespace(generate=lambda *args, **kwargs: json.dumps({
        "left_items": ["a", "b", "c"], "right_items": ["x", "y", "z"],
        "pairs": {"a": "x", "b": "y", "c": "z"}}))
    model = SimpleNamespace(encode=lambda texts: np.ones((len(texts), 384)))
    generate = generation.make_generate_fn(None, "10", "Science", "chapter", provider, None, model)
    result = generate(AllocationLine("topic", "easy", "matching", 1), 1)
    assert result[0]["question_id"] == "matching-one"
    assert accepted[0]["text"].startswith("Match each item")
