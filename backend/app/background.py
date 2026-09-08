"""Section 2's latency-critical execution rules: background overgeneration
(task 10a) and parallel generation across allocation lines (task 10b).

Both use threading (the Anthropic SDK call is a blocking I/O call — a
thread pool gets real concurrency here without needing an async client).
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class ParallelGenerationResult:
    results: dict[int, object] = field(default_factory=dict)  # index into `items` -> result
    errors: dict[int, Exception] = field(default_factory=dict)  # index into `items` -> error


def run_in_background(fn: Callable[..., None], *args, **kwargs) -> threading.Thread:
    """Task 10a: fire-and-forget a background task. Never sits on the
    caller's path — starts a daemon thread and returns immediately. A
    failure inside `fn` is caught and logged, never raised to the caller
    (PLAN.md section 2: "If background banking fails, it's a silent
    no-op... log for later")."""

    def wrapper() -> None:
        try:
            fn(*args, **kwargs)
        except Exception:
            logger.exception("background task failed (silent no-op, per PLAN.md section 2)")

    thread = threading.Thread(target=wrapper, daemon=True)
    thread.start()
    return thread


def generate_parallel(items: list[T], generate_fn: Callable[[T], object]) -> ParallelGenerationResult:
    """Task 10b: fire generate_fn concurrently for each item (one per
    allocation line needing generation), not sequentially — total wait
    approx. one call's duration, not the sum (PLAN.md section 2 latency
    rules). A failure for one item doesn't cancel the others (caller
    decides how to handle per-item failures, e.g. via
    generate_with_reliability already having handled retries/fallback
    inside generate_fn)."""
    outcome = ParallelGenerationResult()
    with ThreadPoolExecutor(max_workers=max(1, len(items))) as executor:
        future_to_index = {executor.submit(generate_fn, item): i for i, item in enumerate(items)}
        for future in as_completed(future_to_index):
            i = future_to_index[future]
            try:
                outcome.results[i] = future.result()
            except Exception as e:  # noqa: BLE001 — collected per-item, not fatal to the batch
                outcome.errors[i] = e
    return outcome
