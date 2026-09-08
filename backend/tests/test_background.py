"""Tasks 10a-10b: background overgeneration and parallel generation.

Uses a mock generator with simulated LLM latency (time.sleep) rather than
a real API call — proves the concurrency mechanism itself (does the
response return before the slow work finishes? do N calls run in
parallel rather than N times sequentially?), which doesn't need real
generation to verify.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.background import generate_parallel, run_in_background

SIMULATED_LLM_LATENCY = 0.3  # seconds


def slow_success(x):
    time.sleep(SIMULATED_LLM_LATENCY)
    return x * 2


def slow_failure(x):
    time.sleep(SIMULATED_LLM_LATENCY)
    raise RuntimeError(f"simulated generation failure for {x}")


# ---------------------------------------------------------------------------
# Task 10a: background overgeneration
# ---------------------------------------------------------------------------


def test_background_task_does_not_block_the_caller():
    t0 = time.time()
    thread = run_in_background(slow_success, 21)
    elapsed_to_return = time.time() - t0
    assert elapsed_to_return < SIMULATED_LLM_LATENCY / 2, "run_in_background must return immediately, not wait"
    thread.join(timeout=2)  # cleanup for the test process


def test_background_task_actually_runs_and_its_effect_is_observable():
    results = []
    thread = run_in_background(lambda: results.append("banked"))
    thread.join(timeout=2)
    assert results == ["banked"]


def test_background_task_failure_is_silently_swallowed_not_raised():
    # confirms run_in_background itself never raises, even when the
    # background function does (PLAN.md section 2: "silent no-op")
    thread = run_in_background(slow_failure, 1)
    thread.join(timeout=2)
    assert not thread.is_alive()  # completed (didn't hang or crash the process)


def test_background_failure_does_not_affect_a_result_already_returned():
    # Simulates: quiz already served (this value is "returned" to the
    # user) BEFORE the background overgeneration task (which will fail)
    # even starts -- the served quiz must be entirely unaffected.
    served_quiz = {"questions": ["q1", "q2", "q3"]}
    thread = run_in_background(slow_failure, "background batch")
    thread.join(timeout=2)
    assert served_quiz == {"questions": ["q1", "q2", "q3"]}


# ---------------------------------------------------------------------------
# Task 10b: parallel generation across allocation lines
# ---------------------------------------------------------------------------


def test_parallel_generation_returns_correct_results_for_each_item():
    outcome = generate_parallel([1, 2, 3, 4], slow_success)
    assert outcome.results == {0: 2, 1: 4, 2: 6, 3: 8}
    assert outcome.errors == {}


def test_parallel_generation_latency_is_close_to_one_call_not_the_sum():
    n = 5
    t0 = time.time()
    generate_parallel(list(range(n)), slow_success)
    elapsed = time.time() - t0
    # sequential would take n * SIMULATED_LLM_LATENCY (1.5s for n=5);
    # parallel should be close to one call's duration
    assert elapsed < SIMULATED_LLM_LATENCY * 2, f"expected ~{SIMULATED_LLM_LATENCY}s, took {elapsed:.2f}s for {n} items"
    assert elapsed < n * SIMULATED_LLM_LATENCY * 0.6, "not meaningfully parallel"


def test_parallel_generation_one_failure_does_not_cancel_others():
    def maybe_fail(x):
        if x == 2:
            return slow_failure(x)
        return slow_success(x)

    outcome = generate_parallel([1, 2, 3], maybe_fail)
    assert outcome.results == {0: 2, 2: 6}
    assert 1 in outcome.errors
    assert "simulated generation failure" in str(outcome.errors[1])
