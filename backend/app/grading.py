"""Section 2 Step E / SPEC.md section 8: per-question grading rules.

Accepted-answer matching uses reviewed equivalents only. Reviewed chemical
aliases work for older bank payloads too, without regrading past attempts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from app.answer_aliases import accepted_answers_for, chemical_identity, looks_like_formula, normalize_answer

_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")


def _normalize_numeric(text: str) -> str | None:
    """Strip commas/units/whitespace (SPEC section 8) and check it's a
    plain number; returns None if it isn't numeric at all."""
    cleaned = re.sub(r"[,\s]", "", text.strip())
    cleaned = re.sub(r"[a-zA-Z%°]+$", "", cleaned)  # trailing unit, e.g. "10kg" -> "10"
    return cleaned if _NUMERIC_RE.match(cleaned) else None


def _normalize_text(text: str) -> str:
    return normalize_answer(text).casefold()


def grade_mcq(payload: dict, user_answer: Any) -> bool:
    """payload: {"correct_option_index": int}. user_answer: the selected
    option index. SPEC section 8: exact match against the correct option."""
    return user_answer == payload["correct_option_index"]


def grade_fill_in_blank(payload: dict, user_answer: str, embed_model=None) -> bool:
    """payload: {"answer": str}. SPEC section 8:
    - numeric answers: exact match after normalizing (strip commas/units), no tolerance.
    - text answers: exact normalized match or a reviewed equivalent.
    """
    correct = normalize_answer(payload["answer"])
    user_answer = normalize_answer(str(user_answer))
    if not user_answer:
        return False
    correct_numeric = _normalize_numeric(correct)
    if correct_numeric is not None:
        user_numeric = _normalize_numeric(str(user_answer))
        return user_numeric is not None and user_numeric == correct_numeric

    identity = chemical_identity(correct)
    if identity:
        return chemical_identity(user_answer) == identity

    alternatives = [correct, *accepted_answers_for(payload)]
    for candidate in alternatives:
        if looks_like_formula(candidate) or chemical_identity(candidate):
            if normalize_answer(candidate) == user_answer:
                return True
        elif _normalize_text(user_answer) == _normalize_text(candidate):
            return True
    # A different formula is a different answer, even if its embedding is close.
    if looks_like_formula(correct) or looks_like_formula(user_answer) or chemical_identity(user_answer):
        return False
    # Similarity measures relatedness, not factual equivalence (atoms/molecules).
    # Keep embed_model in the signature for compatibility, but never use it to grade.
    return False


@dataclass
class MatchingResult:
    all_correct: bool
    row_results: list[bool]  # per-pair green(True)/red(False) — SPEC section 8


def grade_matching(payload: dict, user_pairs: dict[str, str]) -> MatchingResult:
    """payload: {"pairs": {left: right, ...}}. SPEC section 8: worth
    exactly 1 mark total, full credit only if every pair is correct, but
    every row still gets its own green/red feedback marker."""
    correct_pairs = payload["pairs"]
    row_results = [user_pairs.get(left) == right for left, right in correct_pairs.items()]
    return MatchingResult(all_correct=all(row_results), row_results=row_results)


@dataclass
class GradedAnswer:
    question_id: str
    question_type: str
    topic: str
    # Carried alongside topic (not just for display) because topic NAMES
    # are not globally unique across the corpus -- e.g. "Introduction" is a
    # real topic name shared by 255 different chapters (found auditing
    # classes 11-12) -- so performance_counters keys on (chapter, topic),
    # not topic alone (see scoring.py's _update_performance_counters).
    chapter: str
    is_correct: bool
    detail: Any = None  # MatchingResult for matching questions, else None


def grade_answer(
    question_type: str, question_id: str, topic: str, chapter: str, payload: dict, user_answer: Any, embed_model=None
) -> GradedAnswer:
    if question_type == "MCQ":
        return GradedAnswer(question_id, question_type, topic, chapter, grade_mcq(payload, user_answer))
    if question_type == "fill_in_blank":
        return GradedAnswer(question_id, question_type, topic, chapter, grade_fill_in_blank(payload, user_answer, embed_model))
    if question_type == "matching":
        result = grade_matching(payload, user_answer)
        return GradedAnswer(question_id, question_type, topic, chapter, result.all_correct, detail=result)
    raise ValueError(f"unknown question_type: {question_type}")
