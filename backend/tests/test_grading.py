"""SPEC.md section 8 grading rules."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.grading import grade_fill_in_blank, grade_matching, grade_mcq


def test_mcq_exact_match():
    payload = {"correct_option_index": 2}
    assert grade_mcq(payload, 2) is True
    assert grade_mcq(payload, 0) is False


def test_fill_in_blank_numeric_exact():
    assert grade_fill_in_blank({"answer": "10"}, "10") is True


def test_fill_in_blank_numeric_strips_commas_and_units():
    # SPEC section 8: "strip commas/units", no tolerance range
    assert grade_fill_in_blank({"answer": "10000"}, "10,000") is True
    assert grade_fill_in_blank({"answer": "10"}, "10 kg") is True
    assert grade_fill_in_blank({"answer": "98"}, "98%") is True


def test_fill_in_blank_numeric_no_tolerance():
    assert grade_fill_in_blank({"answer": "10"}, "11") is False
    assert grade_fill_in_blank({"answer": "10"}, "10.01") is False


def test_fill_in_blank_text_exact_case_and_whitespace_insensitive():
    assert grade_fill_in_blank({"answer": "Photosynthesis"}, "  photosynthesis  ") is True


def test_fill_in_blank_text_wrong_without_model_rejected():
    assert grade_fill_in_blank({"answer": "oxygen"}, "nitrogen") is False


def test_fill_in_blank_text_synonym_accepted_with_model(embed_model):
    # empirically validated pairs (see task 12 dev notes): real synonyms/
    # close phrasings score 0.81-0.90 with this model, comfortably over
    # the 0.75 threshold
    assert grade_fill_in_blank({"answer": "large"}, "big", embed_model) is True
    assert grade_fill_in_blank({"answer": "H2 gas"}, "hydrogen gas", embed_model) is True
    assert grade_fill_in_blank({"answer": "the process of photosynthesis"}, "photosynthesis", embed_model) is True


def test_fill_in_blank_text_wrong_answer_rejected_even_with_model(embed_model):
    # same-domain but wrong terms score 0.4-0.68 -- comfortably under threshold
    assert grade_fill_in_blank({"answer": "oxygen"}, "nitrogen", embed_model) is False
    assert grade_fill_in_blank({"answer": "respiration"}, "photosynthesis", embed_model) is False
    assert grade_fill_in_blank({"answer": "big"}, "small", embed_model) is False, "antonym must not be accepted as a synonym"


def test_matching_all_correct():
    payload = {"pairs": {"Zn": "Zinc", "H": "Hydrogen"}}
    result = grade_matching(payload, {"Zn": "Zinc", "H": "Hydrogen"})
    assert result.all_correct is True
    assert result.row_results == [True, True]


def test_matching_partial_is_not_all_correct_but_rows_marked_individually():
    # SPEC section 8: worth exactly 1 mark total, full credit only if
    # EVERY pair is correct -- no partial credit -- but each row still
    # gets its own green/red marker.
    payload = {"pairs": {"Zn": "Zinc", "H": "Hydrogen"}}
    result = grade_matching(payload, {"Zn": "Zinc", "H": "Wrong"})
    assert result.all_correct is False
    assert result.row_results == [True, False]


def test_matching_missing_pair_marked_incorrect():
    payload = {"pairs": {"Zn": "Zinc", "H": "Hydrogen"}}
    result = grade_matching(payload, {"Zn": "Zinc"})  # "H" left unanswered
    assert result.all_correct is False
    assert result.row_results == [True, False]
