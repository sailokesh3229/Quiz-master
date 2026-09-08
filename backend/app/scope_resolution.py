"""Section 2, Step A: resolve a quiz request into a concrete allocation list.

Task 1: coverage rule + equal-weighting/round-robin, no accuracy data.
Task 2: accuracy-weighted adjustment on top of that base (SPEC 11a), with
random fallback for sub-units below the minimum sample size.

Per SPEC.md:
- section 5 step 6: a request scopes to one topic, several topics, "all
  topics" in a chapter, or "all chapters" in a subject.
- section 8/10: when multiple question types are selected, split the
  count as evenly as possible, remainder to the FIRST selected type.
- section 10a: coverage rule for "all topics"/"all chapters" (question
  count must exceed sub-unit count), equal weighting + round-robin
  remainder as the base distribution, accuracy-weighted adjustment on top
  where enough history exists, and the 25+-sub-unit overflow case (cap at
  the requested count, select via the same accuracy-weighted/random rule).
- section 11a: a sub-unit needs >= MIN_SAMPLES total attempts (summed
  across question types) before its accuracy is trusted for weighting;
  below that, treat as "no data" (random, not treated as weak).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Literal

VALID_QUESTION_COUNTS = (5, 10, 15, 20, 25)
MIN_SAMPLES = 3  # SPEC 11a suggested minimum before a sub-unit's accuracy is trusted

ScopeType = Literal["single_topic", "multi_topic", "all_topics_in_chapter", "all_chapters_in_subject"]
QuestionType = Literal["MCQ", "fill_in_blank", "matching"]
Difficulty = Literal["easy", "medium", "hard"]


@dataclass
class QuizRequest:
    class_: str
    subject: str
    scope_type: ScopeType
    # The sub-units in scope: topic names for single/multi/all_topics_in_chapter,
    # chapter names for all_chapters_in_subject. Always the real textbook
    # names (SPEC section 5) — never generic "Topic 1"/"Chapter 1" labels.
    sub_units: list[str]
    difficulty: Difficulty
    question_count: int
    question_types: list[QuestionType]


@dataclass
class AllocationLine:
    sub_unit: str
    difficulty: str
    question_type: str
    count: int


class ScopeResolutionError(ValueError):
    """Raised when a request violates a hard constraint (coverage rule,
    invalid question count, empty scope) — these should never reach Step A
    if the UI enforces them correctly (SPEC section 10a), but Step A must
    not silently proceed on a malformed request."""


def _validate_request(request: QuizRequest) -> None:
    if request.question_count not in VALID_QUESTION_COUNTS:
        raise ScopeResolutionError(
            f"question_count must be one of {VALID_QUESTION_COUNTS}, got {request.question_count}"
        )
    if not request.sub_units:
        raise ScopeResolutionError("request has no sub-units in scope")
    if not request.question_types:
        raise ScopeResolutionError("request has no question types selected")

    # SPEC 10a coverage rule: for "all topics"/"all chapters", the question
    # count must exceed the sub-unit count — this should already have been
    # enforced by only offering valid fixed counts at the UI step, but
    # Step A re-validates rather than trusting the client.
    #
    # Exception (SPEC 10a's own explicit edge case): when there are more
    # sub-units than the largest valid question count (25) allows, no
    # valid count can ever exceed the sub-unit count — the rule is
    # deliberately WAIVED here, not violated. The app "simply produces the
    # default maximum of 25 questions... full coverage is not guaranteed
    # in this edge case, and that's accepted for v1." The 25+-overflow
    # selection logic (_select_sub_units) handles the resulting partial
    # coverage.
    if request.scope_type in ("all_topics_in_chapter", "all_chapters_in_subject"):
        if request.question_count not in valid_question_counts(len(request.sub_units), coverage_required=True):
            raise ScopeResolutionError(
                f"coverage rule violated: question_count ({request.question_count}) must be "
                f"greater than the number of sub-units ({len(request.sub_units)}) for scope "
                f"'{request.scope_type}'"
            )


def valid_question_counts(sub_unit_count: int, coverage_required: bool) -> list[int]:
    """SPEC 10a: at the 'select number of questions' step, when the user
    has chosen 'all topics'/'all chapters' (coverage_required=True), only
    offer fixed options that satisfy count > sub_unit_count — except SPEC
    10a's own explicit waiver when there are more sub-units than the
    largest fixed option (25) allows, where no fixed count could ever
    satisfy '>' and the rule is waived rather than leaving zero valid
    options. Single source of truth shared with _validate_request's
    server-side re-check, so the UI and the engine can never disagree."""
    if not coverage_required or sub_unit_count >= max(VALID_QUESTION_COUNTS):
        return list(VALID_QUESTION_COUNTS)
    return [c for c in VALID_QUESTION_COUNTS if c > sub_unit_count]


def _topic_accuracy(
    performance_history: dict[tuple[str, str], tuple[int, int]], sub_unit: str, min_samples: int = MIN_SAMPLES
) -> float | None:
    """Accuracy for one sub-unit, summed across all question types (SPEC
    11a: higher-level accuracy is summed from the leaf (topic, type)
    counters). Returns None ("no data") if below min_samples — a
    below-threshold sub-unit is NOT treated as weak, just unweighted."""
    correct = total = 0
    for (topic, _qtype), (c, t) in performance_history.items():
        if topic == sub_unit:
            correct += c
            total += t
    if total < min_samples:
        return None
    return correct / total


def _weighted_sample_without_replacement(
    population: list[str], weights: list[float], k: int, rng: random.Random
) -> list[str]:
    """Weighted random sample of k items without replacement. Used for
    both the 25+-overflow sub-unit selection and (indirectly, one draw at
    a time) the weighted remainder distribution."""
    pool = list(zip(population, weights))
    chosen = []
    for _ in range(min(k, len(pool))):
        total_weight = sum(w for _, w in pool)
        r = rng.uniform(0, total_weight)
        upto = 0.0
        for i, (item, w) in enumerate(pool):
            upto += w
            if upto >= r:
                chosen.append(item)
                pool.pop(i)
                break
        else:
            # floating point edge case: fall back to the last item
            item, _ = pool.pop()
            chosen.append(item)
    return chosen


def _select_sub_units(
    sub_units: list[str],
    question_count: int,
    performance_history: dict[tuple[str, str], tuple[int, int]] | None,
    rng: random.Random,
) -> list[str]:
    """SPEC 10a's 25+-overflow case: when there are more sub-units than
    the question count allows one-per-sub-unit coverage, select exactly
    `question_count` of them. Accuracy-weighted (weak-topic-preferring)
    when history exists, uniform random otherwise."""
    if len(sub_units) <= question_count:
        return list(sub_units)

    if not performance_history:
        return rng.sample(sub_units, question_count)

    weights = [_accuracy_weight(performance_history, su) for su in sub_units]
    return _weighted_sample_without_replacement(sub_units, weights, question_count, rng)


def _accuracy_weight(performance_history: dict[tuple[str, str], tuple[int, int]], sub_unit: str) -> float:
    """Lower accuracy -> higher weight (more likely to be picked as
    'weak'). No/insufficient data -> neutral 0.5 weight, i.e. treated like
    an average-performing topic, never artificially favored or excluded
    (SPEC 11a: 'not treated as weak')."""
    acc = _topic_accuracy(performance_history, sub_unit)
    return 0.5 if acc is None else max(0.0, 1.0 - acc)


def _distribute_counts(
    sub_units: list[str],
    question_count: int,
    performance_history: dict[tuple[str, str], tuple[int, int]] | None,
    rng: random.Random,
    rotation_offset: int | None,
) -> dict[str, int]:
    """Equal weighting per sub-unit, remainder distributed one-at-a-time —
    round-robin (rotating start point) with no history, accuracy-weighted
    (weak sub-units preferred) when history exists (SPEC 10a)."""
    base = question_count // len(sub_units)
    remainder = question_count % len(sub_units)

    counts = {su: base for su in sub_units}
    if remainder == 0:
        return counts

    if performance_history:
        weights = [_accuracy_weight(performance_history, su) for su in sub_units]
        for su in _weighted_sample_without_replacement(sub_units, weights, remainder, rng):
            counts[su] += 1
    else:
        # round-robin: rotate the starting point each call (stateless —
        # SPEC just requires "not always the same one every time", a
        # persistent per-user rotation cursor is a storage-layer concern
        # outside this isolated function) rather than always starting at
        # index 0.
        start = rotation_offset if rotation_offset is not None else rng.randrange(len(sub_units))
        for i in range(remainder):
            su = sub_units[(start + i) % len(sub_units)]
            counts[su] += 1

    return counts


def _distribute_question_types(count: int, question_types: list[str]) -> dict[str, int]:
    """SPEC section 10: split as evenly as possible, remainder to the
    FIRST selected type (e.g. 25 across MCQ/fill-in-blank/matching -> 9/8/8)."""
    n = len(question_types)
    base = count // n
    remainder = count % n
    result = {qt: base for qt in question_types}
    for i in range(remainder):
        result[question_types[i]] += 1
    return result


def resolve_scope(
    request: QuizRequest,
    performance_history: dict[tuple[str, str], tuple[int, int]] | None = None,
    rotation_offset: int | None = None,
    rng: random.Random | None = None,
) -> list[AllocationLine]:
    """Step A: turn a quiz request into a concrete allocation list.

    performance_history: {(topic, question_type): (correct_count,
      total_count)} for this user. None/empty -> task 1 behavior (equal
      weighting + round-robin, no accuracy weighting). Present -> task 2's
      accuracy-weighted adjustment applies to remainder distribution and
      the 25+-overflow sub-unit selection.
    rotation_offset: only used in the no-history path, to make round-robin
      rotation deterministic/testable; a real caller would persist and
      advance this per user+scope between quizzes.
    rng: injectable for deterministic tests; defaults to a fresh Random().
    """
    _validate_request(request)
    rng = rng or random.Random()

    selected = _select_sub_units(request.sub_units, request.question_count, performance_history, rng)
    per_sub_unit = _distribute_counts(selected, request.question_count, performance_history, rng, rotation_offset)

    allocations: list[AllocationLine] = []
    for sub_unit, count in per_sub_unit.items():
        if count == 0:
            continue
        for qtype, qcount in _distribute_question_types(count, request.question_types).items():
            if qcount > 0:
                allocations.append(AllocationLine(sub_unit=sub_unit, difficulty=request.difficulty, question_type=qtype, count=qcount))

    return allocations
