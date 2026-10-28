import json
import pytest

from app.grading import grade_fill_in_blank
from app.prompts import parse_and_validate, SchemaValidationError
from app.quiz_service import _public_payload
from app.answer_aliases import accepted_answers_for


@pytest.mark.parametrize("answer", ["MgO", "mgo", " magnesium oxide ", "MAGNESIUM OXIDE"])
def test_legacy_magnesium_oxide_accepts_formula(answer):
    assert grade_fill_in_blank({"answer": "magnesium oxide"}, answer)


def test_formula_primary_accepts_full_name():
    assert grade_fill_in_blank({"answer": "MgO"}, "magnesium oxide")
    assert grade_fill_in_blank({"answer": "water"}, "H₂O")
    assert grade_fill_in_blank({"answer": "Ca(OH)2"}, "slaked lime")


@pytest.mark.parametrize("correct,wrong", [
    ("CO", "Co"), ("Co", "CO"), ("carbon monoxide", "co"),
    ("cobalt", "co"), ("magnesium oxide", "Mg"), ("water", "H2O2"),
])
def test_wrong_or_ambiguous_formulas_never_use_embeddings(correct, wrong):
    class TooPermissive:
        def encode(self, values):
            raise AssertionError("chemical grading must not call embeddings")
    assert not grade_fill_in_blank({"answer": correct}, wrong, TooPermissive())


def test_text_aliases_short_circuit_embeddings():
    assert grade_fill_in_blank({"answer": "large", "accepted_answers": ["big"]}, " BIG ")
    assert grade_fill_in_blank({"answer": "deoxyribonucleic acid", "accepted_answers": ["DNA"]}, "dna")


def test_unknown_formula_preserves_case():
    assert grade_fill_in_blank({"answer": "FeCl3"}, "FeCl₃")
    assert not grade_fill_in_blank({"answer": "FeCl3"}, "fecl3")


def test_numeric_aliases_cannot_authorize_a_different_number():
    assert not grade_fill_in_blank({"answer": "10", "accepted_answers": ["11"]}, "11")


def test_generated_chemical_aliases_are_reviewed():
    payload = parse_and_validate("fill_in_blank", json.dumps({
        "question": "The product is ____.", "answer": "magnesium oxide",
        "accepted_answers": ["magnesium", "Mg"]}))
    assert payload.accepted_answers == ["MgO"]
    assert not grade_fill_in_blank(payload.model_dump(), "magnesium")


@pytest.mark.parametrize("aliases", [[""], ["side x side"], ["a"] * 9, ["x" * 81]])
def test_invalid_generated_aliases_are_rejected(aliases):
    with pytest.raises(SchemaValidationError):
        parse_and_validate("fill_in_blank", json.dumps({
            "question": "q ____", "answer": "term", "accepted_answers": aliases}))


def test_legacy_solutions_can_expose_current_aliases_without_mutation():
    original = {"answer": "magnesium oxide"}
    assert accepted_answers_for(original) == ["MgO"]
    assert original == {"answer": "magnesium oxide"}


def test_delivered_question_does_not_leak_aliases():
    assert _public_payload("fill_in_blank", {"answer": "magnesium oxide", "accepted_answers": ["MgO"]}) == {}
