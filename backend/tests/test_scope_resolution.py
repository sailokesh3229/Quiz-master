"""Tests for Section 2 task 1 (coverage rule + equal-weighting/round-robin)
and task 2 (accuracy-weighted adjustment)."""

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.scope_resolution import (
    QuizRequest,
    ScopeResolutionError,
    _distribute_question_types,
    resolve_scope,
)

# ---------------------------------------------------------------------------
# Task 1: coverage rule, equal weighting, round-robin remainder
# ---------------------------------------------------------------------------


def test_single_topic_all_questions_go_to_it():
    req = QuizRequest(
        class_="10", subject="Science", scope_type="single_topic",
        sub_units=["Chemical Equations"], difficulty="medium",
        question_count=10, question_types=["MCQ"],
    )
    allocations = resolve_scope(req, rng=random.Random(0))
    assert sum(a.count for a in allocations) == 10
    assert {a.sub_unit for a in allocations} == {"Chemical Equations"}


def test_equal_weighting_across_topics_that_divide_evenly():
    req = QuizRequest(
        class_="10", subject="Science", scope_type="multi_topic",
        sub_units=["A", "B", "C", "D"], difficulty="medium",
        question_count=20, question_types=["MCQ"],
    )
    allocations = resolve_scope(req, rng=random.Random(0))
    counts = {a.sub_unit: a.count for a in allocations}
    assert counts == {"A": 5, "B": 5, "C": 5, "D": 5}


def test_round_robin_remainder_distributes_one_extra_each():
    # 10 questions / 3 topics = base 3, remainder 1 -> exactly one topic gets 4
    req = QuizRequest(
        class_="10", subject="Science", scope_type="multi_topic",
        sub_units=["A", "B", "C"], difficulty="medium",
        question_count=10, question_types=["MCQ"],
    )
    allocations = resolve_scope(req, rng=random.Random(0))
    counts = {a.sub_unit: a.count for a in allocations}
    assert sum(counts.values()) == 10
    assert sorted(counts.values()) == [3, 3, 4]


def test_round_robin_rotates_which_topic_gets_the_remainder():
    # Explicit rotation_offset controls which topic gets the extra one —
    # confirms it's not hardcoded to always favor the first sub-unit.
    req = QuizRequest(
        class_="10", subject="Science", scope_type="multi_topic",
        sub_units=["A", "B", "C"], difficulty="medium",
        question_count=10, question_types=["MCQ"],
    )
    winners = set()
    for offset in range(3):
        allocations = resolve_scope(req, rotation_offset=offset)
        winner = max({a.sub_unit: a.count for a in allocations}.items(), key=lambda kv: kv[1])[0]
        winners.add(winner)
    assert winners == {"A", "B", "C"}, "remainder should rotate across all topics, not always land on one"


def test_coverage_rule_rejects_all_topics_with_too_few_questions():
    # 6 topics, "all topics" scope, but question_count (5) is not > 6
    req = QuizRequest(
        class_="10", subject="Science", scope_type="all_topics_in_chapter",
        sub_units=["T1", "T2", "T3", "T4", "T5", "T6"], difficulty="medium",
        question_count=5, question_types=["MCQ"],
    )
    with pytest.raises(ScopeResolutionError):
        resolve_scope(req)


def test_coverage_rule_accepts_all_topics_with_enough_questions():
    req = QuizRequest(
        class_="10", subject="Science", scope_type="all_topics_in_chapter",
        sub_units=["T1", "T2", "T3", "T4", "T5", "T6"], difficulty="medium",
        question_count=10, question_types=["MCQ"],
    )
    allocations = resolve_scope(req, rng=random.Random(0))
    assert sum(a.count for a in allocations) == 10
    assert {a.sub_unit for a in allocations} == {"T1", "T2", "T3", "T4", "T5", "T6"}


def test_invalid_question_count_rejected():
    req = QuizRequest(
        class_="10", subject="Science", scope_type="single_topic",
        sub_units=["A"], difficulty="medium", question_count=7, question_types=["MCQ"],
    )
    with pytest.raises(ScopeResolutionError):
        resolve_scope(req)


def test_25plus_overflow_caps_at_question_count_with_no_history():
    # 30 chapters, 25-question cap -> exactly 25 distinct chapters selected,
    # each gets exactly 1 question (SPEC 10a's "more sub-units than the cap" case)
    sub_units = [f"Ch{i}" for i in range(30)]
    req = QuizRequest(
        class_="10", subject="Science", scope_type="all_chapters_in_subject",
        sub_units=sub_units, difficulty="medium", question_count=25, question_types=["MCQ"],
    )
    allocations = resolve_scope(req, rng=random.Random(0))
    assert sum(a.count for a in allocations) == 25
    assert len({a.sub_unit for a in allocations}) == 25


def test_question_type_distribution_remainder_to_first_type():
    # SPEC section 10 worked example: 25 across MCQ/fill-in-blank/matching -> 9/8/8
    result = _distribute_question_types(25, ["MCQ", "fill_in_blank", "matching"])
    assert result == {"MCQ": 9, "fill_in_blank": 8, "matching": 8}


def test_question_type_distribution_even_split():
    result = _distribute_question_types(12, ["MCQ", "fill_in_blank"])
    assert result == {"MCQ": 6, "fill_in_blank": 6}


def test_exact_requested_count_always_delivered_various_shapes():
    # SPEC section 12: user always receives exactly the requested count.
    for sub_unit_n in (1, 2, 3, 7):
        for qcount in (5, 10, 15, 20, 25):
            req = QuizRequest(
                class_="10", subject="Science", scope_type="multi_topic",
                sub_units=[f"T{i}" for i in range(sub_unit_n)], difficulty="hard",
                question_count=qcount, question_types=["MCQ", "matching"],
            )
            allocations = resolve_scope(req, rng=random.Random(1))
            assert sum(a.count for a in allocations) == qcount


# ---------------------------------------------------------------------------
# Task 2: accuracy-weighted adjustment
# ---------------------------------------------------------------------------


def test_weak_topic_gets_the_remainder_over_many_trials():
    # Topic "Weak" has 20% accuracy (well over min sample), "Strong" has 90%.
    # Over many trials with different RNG seeds, "Weak" should win the
    # single remainder slot far more often than chance (1/2).
    history = {
        ("Weak", "MCQ"): (2, 10),
        ("Strong", "MCQ"): (9, 10),
    }
    req = QuizRequest(
        class_="10", subject="Science", scope_type="multi_topic",
        sub_units=["Weak", "Strong"], difficulty="medium",
        question_count=5, question_types=["MCQ"],  # base 2 each, remainder 1
    )
    weak_wins = 0
    trials = 200
    for seed in range(trials):
        allocations = resolve_scope(req, performance_history=history, rng=random.Random(seed))
        counts = {a.sub_unit: a.count for a in allocations}
        if counts["Weak"] > counts["Strong"]:
            weak_wins += 1
    assert weak_wins / trials > 0.75, f"expected weak topic to win most remainder draws, got {weak_wins}/{trials}"


def test_below_min_sample_size_falls_back_to_random_not_treated_as_weak():
    # "New" has only 1 attempt (below MIN_SAMPLES=3) with a low score --
    # despite the low raw accuracy, it must NOT be treated as "weak"
    # (SPEC 11a: below threshold = same as no data). "Average" has enough
    # samples at a middling accuracy. Over many trials, "New" should win
    # the remainder roughly as often as an average topic, not dominate the
    # way a confirmed-weak topic would.
    history = {
        ("New", "MCQ"): (0, 1),  # 0% accuracy but only 1 sample -> below MIN_SAMPLES
        ("Average", "MCQ"): (5, 10),  # 50% accuracy, plenty of samples
    }
    req = QuizRequest(
        class_="10", subject="Science", scope_type="multi_topic",
        sub_units=["New", "Average"], difficulty="medium",
        question_count=5, question_types=["MCQ"],
    )
    new_wins = 0
    trials = 300
    for seed in range(trials):
        allocations = resolve_scope(req, performance_history=history, rng=random.Random(seed))
        counts = {a.sub_unit: a.count for a in allocations}
        if counts["New"] > counts["Average"]:
            new_wins += 1
    # "New" (neutral 0.5 weight) vs "Average" (weight 1-0.5=0.5) are equal
    # weight -> should split roughly 50/50, nowhere near the >75% a
    # confirmed-weak topic would show in the sibling test above.
    assert 0.35 < new_wins / trials < 0.65, f"expected roughly even split, got {new_wins}/{trials}"


def test_25plus_overflow_prefers_weak_chapters_with_history():
    # 30 chapters; 5 of them are confirmed weak (accuracy 10%, enough
    # samples). With a 25-question cap, weak chapters should be selected
    # far more reliably than the other 25 "no data" chapters competing for
    # 20 remaining slots.
    weak = [f"Weak{i}" for i in range(5)]
    unknown = [f"Unk{i}" for i in range(25)]
    history = {(w, "MCQ"): (1, 10) for w in weak}
    req = QuizRequest(
        class_="10", subject="Science", scope_type="all_chapters_in_subject",
        sub_units=weak + unknown, difficulty="medium",
        question_count=25, question_types=["MCQ"],
    )
    weak_included_counts = []
    for seed in range(50):
        allocations = resolve_scope(req, performance_history=history, rng=random.Random(seed))
        selected = {a.sub_unit for a in allocations}
        weak_included_counts.append(len(selected & set(weak)))
    avg_weak_included = sum(weak_included_counts) / len(weak_included_counts)
    assert avg_weak_included > 4.0, f"expected nearly all 5 weak chapters to be selected on average, got {avg_weak_included}"


def test_no_history_behaves_exactly_like_task_1():
    req = QuizRequest(
        class_="10", subject="Science", scope_type="multi_topic",
        sub_units=["A", "B", "C"], difficulty="medium",
        question_count=10, question_types=["MCQ"],
    )
    with_none = resolve_scope(req, performance_history=None, rotation_offset=0)
    with_empty = resolve_scope(req, performance_history={}, rotation_offset=0)
    to_counts = lambda allocs: {a.sub_unit: a.count for a in allocs}
    assert to_counts(with_none) == to_counts(with_empty)
