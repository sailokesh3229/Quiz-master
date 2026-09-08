"""Tasks 4-6: prompt templates + schema validation per question type.

Uses hand-crafted mock LLM responses (valid and deliberately malformed)
to prove the prompt-building and schema-validation logic. Real-generation
verification against actual Claude output is deferred until Anthropic
credits are available — see prompts.py's module docstring.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.prompts import (
    PROMPT_BUILDERS,
    SchemaValidationError,
    build_fill_in_blank_prompt,
    build_matching_prompt,
    build_mcq_prompt,
    parse_and_validate,
)


def test_mcq_prompt_includes_grounding_chunk_and_difficulty(sample_chunk_text):
    prompt = build_mcq_prompt(sample_chunk_text, "medium")
    assert sample_chunk_text in prompt
    assert "medium" in prompt


def test_fill_in_blank_prompt_includes_grounding_chunk(sample_chunk_text):
    prompt = build_fill_in_blank_prompt(sample_chunk_text, "hard")
    assert sample_chunk_text in prompt
    assert "single word or short term" in prompt.lower() or "single" in prompt.lower()


def test_matching_prompt_includes_grounding_chunk(sample_chunk_text):
    prompt = build_matching_prompt(sample_chunk_text, "easy")
    assert sample_chunk_text in prompt


def test_all_three_types_have_a_prompt_builder():
    assert set(PROMPT_BUILDERS.keys()) == {"MCQ", "fill_in_blank", "matching"}


# ---------------------------------------------------------------------------
# MCQ schema
# ---------------------------------------------------------------------------


def test_mcq_valid_output_parses():
    raw = json.dumps({
        "question": "Which gas is produced when zinc reacts with dilute sulphuric acid?",
        "options": ["Oxygen", "Hydrogen", "Nitrogen", "Carbon dioxide"],
        "correct_option_index": 1,
    })
    payload = parse_and_validate("MCQ", raw)
    assert payload.correct_option_index == 1
    assert len(payload.options) == 4


def test_mcq_strips_markdown_code_fence():
    raw = "```json\n" + json.dumps({
        "question": "q", "options": ["a", "b", "c", "d"], "correct_option_index": 0,
    }) + "\n```"
    payload = parse_and_validate("MCQ", raw)
    assert payload.question == "q"


def test_mcq_wrong_option_count_rejected():
    raw = json.dumps({"question": "q", "options": ["a", "b", "c"], "correct_option_index": 0})
    with pytest.raises(SchemaValidationError):
        parse_and_validate("MCQ", raw)


def test_mcq_out_of_range_index_rejected():
    raw = json.dumps({"question": "q", "options": ["a", "b", "c", "d"], "correct_option_index": 5})
    with pytest.raises(SchemaValidationError):
        parse_and_validate("MCQ", raw)


def test_mcq_malformed_json_rejected():
    with pytest.raises(SchemaValidationError):
        parse_and_validate("MCQ", "this is not json at all")


# ---------------------------------------------------------------------------
# Fill-in-blank schema
# ---------------------------------------------------------------------------


def test_fill_in_blank_valid_output_parses():
    raw = json.dumps({"question": "Zinc + dilute H2SO4 -> ____ + ZnSO4", "answer": "hydrogen"})
    payload = parse_and_validate("fill_in_blank", raw)
    assert payload.answer == "hydrogen"


def test_fill_in_blank_multiword_compound_term_allowed():
    raw = json.dumps({"question": "q ____", "answer": "carbon dioxide gas"})
    payload = parse_and_validate("fill_in_blank", raw)
    assert payload.answer == "carbon dioxide gas"


def test_fill_in_blank_full_sentence_answer_rejected():
    # SPEC section 8 / PLAN section 3: answer must be a single term, not a phrase/sentence
    raw = json.dumps({"question": "q ____", "answer": "It produces hydrogen gas when heated strongly."})
    with pytest.raises(SchemaValidationError):
        parse_and_validate("fill_in_blank", raw)


def test_fill_in_blank_numeric_answer_allowed():
    # SPEC section 9a: computational concepts should end up numeric.
    raw = json.dumps({"question": "A square has a side of 6 cm. What is its area, in cm²? ____", "answer": "36"})
    payload = parse_and_validate("fill_in_blank", raw)
    assert payload.answer == "36"


@pytest.mark.parametrize(
    "answer",
    ["side x side", "side × side", "(side)^2", "l x b", "length * breadth", "l/b", "a=b"],
)
def test_fill_in_blank_symbolic_formula_answer_rejected(answer):
    # SPEC section 9a: a formula-shaped answer has many equally-correct
    # phrasings a similarity grader can't reconcile — reject at generation
    # time instead, forcing a retry into a numeric/bare-term answer.
    raw = json.dumps({"question": "q ____", "answer": answer})
    with pytest.raises(SchemaValidationError):
        parse_and_validate("fill_in_blank", raw)


def test_fill_in_blank_chemical_formula_answer_still_allowed():
    # A bare fact/notation answer (no operator) must not be caught by the
    # symbolic-formula rejection above.
    raw = json.dumps({"question": "The chemical formula for water is ____", "answer": "H2O"})
    payload = parse_and_validate("fill_in_blank", raw)
    assert payload.answer == "H2O"


# ---------------------------------------------------------------------------
# Matching schema
# ---------------------------------------------------------------------------


def test_matching_valid_output_parses():
    raw = json.dumps({
        "left_items": ["Zn", "H2SO4", "ZnSO4", "H2"],
        "right_items": ["Zinc", "Sulphuric acid", "Zinc sulphate", "Hydrogen"],
        "pairs": {"Zn": "Zinc", "H2SO4": "Sulphuric acid", "ZnSO4": "Zinc sulphate", "H2": "Hydrogen"},
    })
    payload = parse_and_validate("matching", raw)
    assert len(payload.pairs) == 4


def test_matching_mismatched_lengths_rejected():
    raw = json.dumps({
        "left_items": ["a", "b", "c"],
        "right_items": ["x", "y"],
        "pairs": {"a": "x", "b": "y", "c": "x"},
    })
    with pytest.raises(SchemaValidationError):
        parse_and_validate("matching", raw)


def test_matching_pairs_not_covering_every_left_item_rejected():
    raw = json.dumps({
        "left_items": ["a", "b", "c"],
        "right_items": ["x", "y", "z"],
        "pairs": {"a": "x", "b": "y"},  # missing "c"
    })
    with pytest.raises(SchemaValidationError):
        parse_and_validate("matching", raw)


def test_matching_right_item_reused_rejected():
    # pairs must use every right item exactly once -- a reused right item
    # means some right item was never used, which breaks the 1:1 mapping
    # the drag-and-drop UI needs (SPEC section 8)
    raw = json.dumps({
        "left_items": ["a", "b", "c"],
        "right_items": ["x", "y", "z"],
        "pairs": {"a": "x", "b": "x", "c": "z"},
    })
    with pytest.raises(SchemaValidationError):
        parse_and_validate("matching", raw)


def test_matching_too_few_pairs_rejected():
    # PLAN section 3: matching needs "4-5" pairs
    raw = json.dumps({
        "left_items": ["a", "b"],
        "right_items": ["x", "y"],
        "pairs": {"a": "x", "b": "y"},
    })
    with pytest.raises(SchemaValidationError):
        parse_and_validate("matching", raw)
