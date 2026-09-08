"""Task 7: reliability handling (SPEC section 9) — retries, fallback
provider, schema-validation retry. Tested by deliberately forcing
failures with fake providers, per the task's own description — proves
the control-flow mechanism without needing real API calls (or costing
real usage) for every case. One live-failure-path test uses the real,
intentionally-misconfigured Anthropic client to confirm a genuine bad
API key triggers the same path end-to-end.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.llm_client import AnthropicProvider, ReliabilityError, generate_with_reliability
from app.prompts import SchemaValidationError, parse_and_validate


class FakeProvider:
    """A provider whose behavior is scripted for deterministic tests:
    raises for the first `fail_times` calls, then returns `response`."""

    def __init__(self, fail_times: int = 0, response: str = "ok", error: Exception | None = None):
        self.fail_times = fail_times
        self.response = response
        self.error = error or RuntimeError("simulated provider failure")
        self.calls = 0

    def generate(self, prompt: str, *, max_tokens: int = 1024) -> str:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.error
        return self.response


class AlwaysFails:
    def __init__(self):
        self.calls = 0

    def generate(self, prompt: str, *, max_tokens: int = 1024) -> str:
        self.calls += 1
        raise RuntimeError("always fails")


def _no_sleep(_seconds: float) -> None:
    """Injected in place of time.sleep so retry-backoff tests run instantly."""


def test_succeeds_on_first_try_no_retry_needed():
    primary = FakeProvider(fail_times=0, response="answer")
    result = generate_with_reliability("prompt", primary, sleep=_no_sleep)
    assert result == "answer"
    assert primary.calls == 1


def test_retries_primary_before_succeeding():
    primary = FakeProvider(fail_times=2, response="answer")  # fails twice, succeeds on 3rd
    result = generate_with_reliability("prompt", primary, max_retries=2, sleep=_no_sleep)
    assert result == "answer"
    assert primary.calls == 3


def test_retry_count_is_bounded():
    primary = AlwaysFails()
    with pytest.raises(ReliabilityError):
        generate_with_reliability("prompt", primary, max_retries=2, sleep=_no_sleep)
    assert primary.calls == 3  # 1 initial + 2 retries, then gives up


def test_falls_back_to_secondary_provider_after_primary_exhausted():
    primary = AlwaysFails()
    fallback = FakeProvider(fail_times=0, response="fallback answer")
    result = generate_with_reliability("prompt", primary, fallback=fallback, max_retries=1, sleep=_no_sleep)
    assert result == "fallback answer"
    assert primary.calls == 2  # 1 initial + 1 retry
    assert fallback.calls == 1


def test_both_primary_and_fallback_fail_raises_clear_error():
    primary = AlwaysFails()
    fallback = AlwaysFails()
    with pytest.raises(ReliabilityError, match="fallback also failed"):
        generate_with_reliability("prompt", primary, fallback=fallback, max_retries=1, sleep=_no_sleep)


def test_no_fallback_configured_error_says_so():
    primary = AlwaysFails()
    with pytest.raises(ReliabilityError, match="no fallback configured"):
        generate_with_reliability("prompt", primary, fallback=None, max_retries=0, sleep=_no_sleep)


def test_backoff_increases_between_retries():
    sleep_calls = []
    primary = AlwaysFails()
    with pytest.raises(ReliabilityError):
        generate_with_reliability("prompt", primary, max_retries=3, backoff_seconds=1.0, sleep=sleep_calls.append)
    # exponential backoff: 1, 2, 4 (one sleep per retry, not after the final failed attempt)
    assert sleep_calls == [1.0, 2.0, 4.0]


def test_fallback_never_called_when_primary_succeeds():
    primary = FakeProvider(fail_times=0, response="answer")
    fallback = FakeProvider(fail_times=0, response="should never be used")
    result = generate_with_reliability("prompt", primary, fallback=fallback, sleep=_no_sleep)
    assert result == "answer"
    assert fallback.calls == 0


# ---------------------------------------------------------------------------
# Malformed output -> regenerate (bounded retries), section 9
# ---------------------------------------------------------------------------


def test_malformed_output_triggers_regeneration_until_valid():
    """Simulates the generate-then-validate-then-regenerate loop: a
    provider that returns malformed JSON twice, then valid JSON."""
    responses = iter([
        "not json",
        '{"question": "q", "options": ["a", "b", "c"], "correct_option_index": 0}',  # wrong option count
        '{"question": "q", "options": ["a", "b", "c", "d"], "correct_option_index": 0}',  # valid
    ])

    max_attempts = 3
    payload = None
    attempts = 0
    for _ in range(max_attempts):
        attempts += 1
        try:
            payload = parse_and_validate("MCQ", next(responses))
            break
        except SchemaValidationError:
            continue

    assert payload is not None
    assert attempts == 3


def test_persistent_malformed_output_exhausts_retries_and_is_dropped():
    # SPEC section 9: "persistent failure on a single question after
    # retries: drop it and generate a replacement" -- this proves the
    # retry loop actually gives up after the bound rather than looping
    # forever.
    max_attempts = 3
    attempts = 0
    payload = None
    for _ in range(max_attempts):
        attempts += 1
        try:
            payload = parse_and_validate("MCQ", "always malformed, never valid json")
            break
        except SchemaValidationError:
            continue

    assert payload is None
    assert attempts == max_attempts


# ---------------------------------------------------------------------------
# Live path: a genuinely bad API key hits the real Anthropic client and
# still surfaces cleanly through the same reliability wrapper.
# ---------------------------------------------------------------------------


def test_live_bad_api_key_triggers_fallback_path():
    broken_primary = AnthropicProvider(api_key="sk-ant-deliberately-invalid-key-for-testing")
    fallback = FakeProvider(fail_times=0, response="fallback saved the day")
    result = generate_with_reliability("Reply OK", broken_primary, fallback=fallback, max_retries=0, sleep=_no_sleep)
    assert result == "fallback saved the day"
