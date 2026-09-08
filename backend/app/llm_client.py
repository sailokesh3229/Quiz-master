"""Section 2, section 4: LLM provider abstraction + reliability handling.

Primary provider: OpenAI (switched 2026-08-30 — Anthropic billing kept
failing; user supplied a working OpenAI key). Anthropic stays wired up as
the concrete secondary/fallback provider (section 4/9's "secondary LLM
provider" — previously undecided, now filled by what was already built as
primary). generate_with_reliability is provider-agnostic (works over any
two callables implementing Provider), proven with synthetic fake
providers in task 7's tests; live calls against both real providers
confirm the end-to-end paths.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Callable, Protocol

import anthropic
import openai

from app.config import load_dotenv

load_dotenv()

PRIMARY_MODEL = "gpt-4o-mini"
FALLBACK_MODEL = "claude-sonnet-5"


class Provider(Protocol):
    def generate(self, prompt: str, *, max_tokens: int = 1024) -> str: ...


@dataclass
class OpenAIProvider:
    model: str = PRIMARY_MODEL
    api_key: str | None = None

    def __post_init__(self) -> None:
        self._client = openai.OpenAI(api_key=self.api_key)

    def generate(self, prompt: str, *, max_tokens: int = 1024) -> str:
        response = self._client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content


@dataclass
class AnthropicProvider:
    model: str = FALLBACK_MODEL
    api_key: str | None = None

    def __post_init__(self) -> None:
        workspace_id = os.environ.get("ANTHROPIC_WORKSPACE_ID")
        default_headers = {"anthropic-workspace-id": workspace_id} if workspace_id else None
        self._client = anthropic.Anthropic(api_key=self.api_key, default_headers=default_headers)

    def generate(self, prompt: str, *, max_tokens: int = 1024) -> str:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text


def default_primary() -> Provider:
    return OpenAIProvider()


def default_fallback() -> Provider:
    return AnthropicProvider()


class ReliabilityError(RuntimeError):
    """Both the primary provider (after retries) and the fallback
    provider failed — SPEC section 9: surface a clear error, never an
    indefinite wait."""


def generate_with_reliability(
    prompt: str,
    primary: Provider,
    fallback: Provider | None = None,
    max_retries: int = 2,
    backoff_seconds: float = 1.0,
    max_tokens: int = 1024,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """SPEC section 9: retry the primary with backoff (bounded attempts),
    then fall back to a secondary provider (sequential, not parallel —
    PLAN.md section 4), then surface a clear error if both fail."""
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            return primary.generate(prompt, max_tokens=max_tokens)
        except Exception as e:  # noqa: BLE001 — deliberately broad: any provider failure triggers retry/fallback
            last_error = e
            if attempt < max_retries:
                sleep(backoff_seconds * (2**attempt))

    if fallback is not None:
        try:
            return fallback.generate(prompt, max_tokens=max_tokens)
        except Exception as e:  # noqa: BLE001
            last_error = e

    raise ReliabilityError(
        f"primary provider failed after {max_retries + 1} attempts"
        + (" and fallback also failed" if fallback is not None else " (no fallback configured)")
        + f": {last_error}"
    )
