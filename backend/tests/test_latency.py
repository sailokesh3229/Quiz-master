"""Task 14: latency verification (SPEC latency target). Measures the
worst realistic case — a large question count (25) on a cold-bank topic
requiring full generation — and confirms it lands in the 10-20s range
with parallel generation (task 10b, wired into generation.py per the
task 14 finding that a naive sequential loop blows the target). Hits the
real OpenAI API — costs a small amount of real usage per run.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.generation import make_generate_fn
from app.llm_client import OpenAIProvider
from app.quiz_engine import assemble_quiz
from app.scope_resolution import QuizRequest

CLASS = "10"
SUBJECT = "Science"
CHAPTER = "Chemical Reactions and Equations"
TOPIC = "CHEMICAL EQUATIONS"


def test_worst_case_25_question_cold_bank_quiz_meets_latency_target(db_conn, embed_model):
    provider = OpenAIProvider()
    generate_fn = make_generate_fn(
        db_conn, class_=CLASS, subject=SUBJECT, chapter=CHAPTER,
        provider=provider, fallback_provider=None, embed_model=embed_model,
    )

    # bank is empty in this test transaction -> full generation, single
    # topic/type/difficulty (the worst case PLAN.md section 2 task 14
    # describes: one allocation line needing the maximum count)
    request = QuizRequest(
        class_=CLASS, subject=SUBJECT, scope_type="single_topic",
        sub_units=[TOPIC], difficulty="medium", question_count=25, question_types=["MCQ"],
    )

    t0 = time.time()
    assembled = assemble_quiz(db_conn, request, chapter=CHAPTER, user_id="latency_test_user", generate_fn=generate_fn)
    elapsed = time.time() - t0

    assert len(assembled) == 25, "SPEC section 12: exact requested count must always be delivered"
    print(f"\n25-question cold-bank generation took {elapsed:.1f}s")
    assert elapsed <= 20.0, f"expected <=20s per SPEC latency target, took {elapsed:.1f}s"


def test_sequential_would_have_missed_the_target_demonstrating_parallel_is_load_bearing(db_conn, embed_model):
    """Not a hard assertion on production code — a documentation test
    showing WHY task 10b's parallelization was necessary: replays the
    same generation work sequentially (1 concurrent worker) to measure
    what the naive loop this replaced would have cost."""
    from app.dedup import intake_question
    from app.llm_client import generate_with_reliability
    from app.prompts import PROMPT_BUILDERS, parse_and_validate
    from app.generation import get_grounding_chunks
    import random

    provider = OpenAIProvider()
    chunks = get_grounding_chunks(CLASS, SUBJECT, TOPIC)
    n = 5  # smaller n for the sequential baseline -- still enough to show the per-call cost

    t0 = time.time()
    for _ in range(n):
        chunk = random.choice(chunks)
        prompt = PROMPT_BUILDERS["MCQ"](chunk["text"], "medium")
        raw = generate_with_reliability(prompt, primary=provider, max_retries=0)
        parse_and_validate("MCQ", raw)
    elapsed = time.time() - t0
    per_call = elapsed / n
    projected_25_sequential = per_call * 25

    print(f"\nsequential baseline: {per_call:.2f}s/call -> {n} calls in {elapsed:.1f}s")
    print(f"projected time for 25 sequential calls: {projected_25_sequential:.1f}s")
    assert projected_25_sequential > 20.0, (
        "expected the sequential projection to exceed the 20s target, confirming parallelization "
        "(task 10b) is load-bearing for task 14's latency requirement, not just an optimization"
    )
