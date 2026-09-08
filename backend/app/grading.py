"""Section 2 Step E / SPEC.md section 8: per-question grading rules.

Resolves SPEC.md section 15's open question ("synonym-matching mechanism
for fill-in-blank text answers... embedding similarity vs. a curated
accepted-answers list vs. an LLM grading call — a PLAN.md-level decision,
not yet made"): **embedding similarity**, via the same sentence-transformers
model already in the stack (Section 1's all-MiniLM-L6-v2) — no extra LLM
call needed for grading (keeps grading fast/cheap/deterministic-ish,
consistent with existing infra), at a conservative similarity threshold.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Cosine similarity threshold for accepting a fill-in-blank text answer as
# a synonym/close phrasing of the correct term (SPEC section 8). Starting
# conservative, same "tune empirically" stance as the duplicate-detection
# threshold (section 5) — not yet tuned against real student answers.
SYNONYM_SIMILARITY_THRESHOLD = 0.75

_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")


def _normalize_numeric(text: str) -> str | None:
    """Strip commas/units/whitespace (SPEC section 8) and check it's a
    plain number; returns None if it isn't numeric at all."""
    cleaned = re.sub(r"[,\s]", "", text.strip())
    cleaned = re.sub(r"[a-zA-Z%°]+$", "", cleaned)  # trailing unit, e.g. "10kg" -> "10"
    return cleaned if _NUMERIC_RE.match(cleaned) else None


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def grade_mcq(payload: dict, user_answer: Any) -> bool:
    """payload: {"correct_option_index": int}. user_answer: the selected
    option index. SPEC section 8: exact match against the correct option."""
    return user_answer == payload["correct_option_index"]


def grade_fill_in_blank(payload: dict, user_answer: str, embed_model=None) -> bool:
    """payload: {"answer": str}. SPEC section 8:
    - numeric answers: exact match after normalizing (strip commas/units), no tolerance.
    - text answers: accept a reasonable synonym/close phrasing, not only exact match.
    """
    correct = payload["answer"]
    correct_numeric = _normalize_numeric(correct)
    if correct_numeric is not None:
        user_numeric = _normalize_numeric(str(user_answer))
        return user_numeric is not None and user_numeric == correct_numeric

    if _normalize_text(str(user_answer)) == _normalize_text(correct):
        return True
    if embed_model is None:
        return False

    import numpy as np

    a, b = embed_model.encode([str(user_answer), correct])
    similarity = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
    return similarity >= SYNONYM_SIMILARITY_THRESHOLD


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
