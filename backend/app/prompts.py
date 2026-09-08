"""Section 2, section 3: prompt templates + schema validation per question
type (tasks 4-6).

Each question type gets its own template — grounded chunk text in,
strict-schema JSON out. All three share: output must validate against a
Pydantic schema, and the caller is responsible for attaching source_chunk_id
(PLAN.md section 3 — "every question must carry a reference to its source
chunk_id for later grounded explanations").

Live-generation verification (2026-08-30, tests/test_live_generation.py):
confirmed against the real OpenAI model (gpt-4o-mini, primary provider —
Anthropic billing was blocked, switched per user) across 2 real Section 1
chunks and 3 difficulties — 9/9 valid on first generation, no retries
needed for any of the three question types.
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field, field_validator, model_validator


class MCQPayload(BaseModel):
    question: str
    options: list[str] = Field(min_length=4, max_length=4)
    correct_option_index: int = Field(ge=0, le=3)


_ALGEBRAIC_OPERATOR_RE = re.compile(r"[\^*/=()×÷]|[²³]")
# "×" is unambiguous even with no surrounding whitespace ("side×side"), but
# a bare "x" needs whitespace on both sides or it false-positives inside
# ordinary words that happen to contain the letter x (e.g. "dioxide").
_WORD_TIMES_WORD_RE = re.compile(r"\b[a-zA-Z]+\s*×\s*[a-zA-Z]+\b|\b[a-zA-Z]+\s+x\s+[a-zA-Z]+\b", re.IGNORECASE)


class FillInBlankPayload(BaseModel):
    question: str
    answer: str

    @field_validator("answer")
    @classmethod
    def answer_is_a_single_term(cls, v: str) -> str:
        # SPEC section 8 / PLAN section 3: "constrain the answer to a
        # single term — not a phrase or sentence". Allow short compound
        # terms ("carbon dioxide") but reject anything sentence-shaped.
        if len(v.split()) > 4 or any(p in v for p in ".!?;"):
            raise ValueError(f"answer must be a single term, not a phrase/sentence: {v!r}")
        return v

    @field_validator("answer")
    @classmethod
    def answer_is_not_a_symbolic_formula(cls, v: str) -> str:
        # SPEC section 9a: "length x length" and "(side)^2" are equally
        # correct but a similarity-based grader can't reliably treat them
        # as equivalent. Reject formula-shaped answers here so generation
        # retries into a numeric or bare-term answer instead (section 9's
        # existing retry path) rather than banking an ungradeable answer.
        if _ALGEBRAIC_OPERATOR_RE.search(v) or _WORD_TIMES_WORD_RE.search(v):
            raise ValueError(f"answer must not be a symbolic/algebraic expression: {v!r}")
        return v


class MatchingPayload(BaseModel):
    left_items: list[str] = Field(min_length=3)
    right_items: list[str] = Field(min_length=3)
    pairs: dict[str, str]

    @model_validator(mode="after")
    def pairs_form_a_bijection_with_items(self) -> "MatchingPayload":
        if len(self.left_items) != len(self.right_items):
            raise ValueError("left_items and right_items must be the same length")
        if set(self.pairs.keys()) != set(self.left_items):
            raise ValueError("pairs must have exactly one entry per left item")
        if set(self.pairs.values()) != set(self.right_items):
            raise ValueError("pairs must use every right item exactly once")
        return self


SCHEMAS = {"MCQ": MCQPayload, "fill_in_blank": FillInBlankPayload, "matching": MatchingPayload}


def _style_reference_block(style_example: str | None) -> str:
    """SPEC.md section 6: a real NCERT-authored exercise question, shown
    purely as a grade-level WORDING/GRAMMAR complexity reference — the
    model must not treat it as a fact to draw on or a template to fill in,
    only as a calibration for how simply a question at this grade level
    should read. Returns "" when no example is available for this chapter
    (most books haven't captured one yet), so the prompt is unaffected."""
    if not style_example:
        return ""
    return f"""

WORDING STYLE REFERENCE (grade-level phrasing only — do not copy its content, facts, or subject matter, it is almost certainly unrelated to the textbook content above):
\"\"\"
{style_example}
\"\"\"
Match how simply and directly this example is worded — not its topic."""


def build_mcq_prompt(chunk_text: str, difficulty: str, style_example: str | None = None) -> str:
    return f"""You are writing a {difficulty}-difficulty multiple-choice question for a school student, based ONLY on the textbook content below. Do not use outside knowledge.

TEXTBOOK CONTENT:
\"\"\"
{chunk_text}
\"\"\"
{_style_reference_block(style_example)}

Write exactly one MCQ grounded in this content, at {difficulty} difficulty. Respond with ONLY a JSON object (no markdown, no explanation) matching this exact shape:
{{"question": "...", "options": ["...", "...", "...", "..."], "correct_option_index": 0}}

- Test whether the student understands and can reason about the concept — not whether they can recall the textbook's exact sentence. Phrase the question in your own words; use the subject's standard terms as-is (they're correct terminology, not "textbook wording"), but do not lightly reword a sentence lifted from the passage.
- Exactly 4 options, plausible distractors (not obviously wrong).
- correct_option_index is the 0-based index of the correct option.
- The question and correct answer must be directly supported by the textbook content above."""


def build_fill_in_blank_prompt(chunk_text: str, difficulty: str, style_example: str | None = None) -> str:
    return f"""You are writing a {difficulty}-difficulty fill-in-the-blank question for a school student, based ONLY on the textbook content below. Do not use outside knowledge.

TEXTBOOK CONTENT:
\"\"\"
{chunk_text}
\"\"\"
{_style_reference_block(style_example)}

Write exactly one fill-in-the-blank question grounded in this content, at {difficulty} difficulty. Test whether the student understands the concept — phrase the question in your own words rather than lightly rewording a sentence from the passage above.

The answer MUST be one of:
- a single word or short term (e.g. "hydrogen", "photosynthesis", "carbon dioxide"), OR
- a plain number, if the concept is computational.

If the concept involves a formula or calculation (e.g. area, speed, percentage), do NOT ask the student to state the formula itself — instead, invent concrete numbers for the scenario and ask for the computed numeric result. For example, instead of "The area of a square is ____" (answer: "side x side" or "(side)^2" — both correct, but not gradable against each other), write "A square has a side of 6 cm. What is its area, in cm²?" (answer: "36"). The answer must NEVER be a symbolic/algebraic expression (no ×, ^, *, /, =, or parentheses) — always a bare term or a bare number.

Respond with ONLY a JSON object (no markdown, no explanation) matching this exact shape:
{{"question": "... ____ ...", "answer": "..."}}

- The question must contain a blank (use "____").
- The answer must be directly supported by the textbook content above."""


def build_matching_prompt(chunk_text: str, difficulty: str, style_example: str | None = None) -> str:
    return f"""You are writing a {difficulty}-difficulty matching question for a school student, based ONLY on the textbook content below. Do not use outside knowledge.

TEXTBOOK CONTENT:
\"\"\"
{chunk_text}
\"\"\"
{_style_reference_block(style_example)}

Write exactly one matching question with 4-5 pairs grounded in this content (e.g. terms<->definitions, reactants<->products, events<->dates — whatever the content actually supports), at {difficulty} difficulty. Respond with ONLY a JSON object (no markdown, no explanation) matching this exact shape:
{{"left_items": ["...", "...", "...", "..."], "right_items": ["...", "...", "...", "..."], "pairs": {{"left item 1": "matching right item", "left item 2": "matching right item", ...}}}}

- Phrase each item (especially definitions) in your own words rather than copying a sentence verbatim from the passage — the pairing should test whether the student recognizes the concept, not whether they memorized the textbook's exact phrasing. Keep the subject's standard terms (names, formulas, dates) as-is.
- left_items and right_items must be the same length (4 or 5).
- pairs must map every left item to exactly one right item, using every right item exactly once.
- Every pair must be directly supported by the textbook content above."""


PROMPT_BUILDERS = {"MCQ": build_mcq_prompt, "fill_in_blank": build_fill_in_blank_prompt, "matching": build_matching_prompt}


class SchemaValidationError(ValueError):
    """Malformed LLM output that failed schema validation (SPEC section
    9) — the caller should regenerate (bounded retries, task 7)."""


def _extract_json(raw_output: str) -> dict:
    """LLMs sometimes wrap JSON in markdown code fences despite
    instructions not to — strip those before parsing."""
    text = raw_output.strip()
    fence_match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise SchemaValidationError(f"LLM output is not valid JSON: {e}") from e


def parse_and_validate(question_type: str, raw_output: str) -> BaseModel:
    schema = SCHEMAS[question_type]
    data = _extract_json(raw_output)
    try:
        return schema.model_validate(data)
    except Exception as e:  # pydantic.ValidationError
        raise SchemaValidationError(f"LLM output failed {question_type} schema validation: {e}") from e
