"""Section 3: quiz-selection metadata, tested against the real Section 1
Chroma store (2927 chunks, 200 chapters, classes 6-10 x 3 subjects —
PARSING_EXCEPTIONS.md)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.catalog import (
    CatalogError,
    count_chapters_in_subject,
    count_topics_in_chapter,
    list_chapters,
    list_subjects,
    list_topics,
    validate_scope,
)

CLASS = "10"
SUBJECT = "Science"
CHAPTER = "Chemical Reactions and Equations"


def test_list_subjects_returns_real_subjects_not_generic_labels():
    subjects = list_subjects(CLASS)
    assert set(subjects) == {"Maths", "Science", "Social Science"}


def test_list_chapters_returns_real_chapter_names():
    chapters = list_chapters(CLASS, SUBJECT)
    names = [c.chapter for c in chapters]
    assert CHAPTER in names
    assert all(not name[0:1].isdigit() or " " in name for name in names), "chapters should be real titles, not 'Chapter 1'"


def test_list_chapters_ordered_by_chapter_number_not_alphabetically():
    chapters = list_chapters(CLASS, SUBJECT)
    numbered = [c for c in chapters if c.chapter_number]
    numeric = [int(c.chapter_number) for c in numbered]
    assert numeric == sorted(numeric)


def test_list_topics_returns_real_topic_names_for_a_chapter():
    topics = list_topics(CLASS, SUBJECT, CHAPTER)
    assert len(topics) > 1
    assert all(t.topic and not t.topic.lower().startswith("topic ") for t in topics)


def test_count_topics_matches_list_length():
    assert count_topics_in_chapter(CLASS, SUBJECT, CHAPTER) == len(list_topics(CLASS, SUBJECT, CHAPTER))


def test_count_chapters_matches_list_length():
    assert count_chapters_in_subject(CLASS, SUBJECT) == len(list_chapters(CLASS, SUBJECT))


def test_validate_scope_accepts_real_scope():
    validate_scope(CLASS, SUBJECT, [CHAPTER])  # should not raise


def test_validate_scope_rejects_unknown_chapter():
    with pytest.raises(CatalogError):
        validate_scope(CLASS, SUBJECT, ["Not A Real Chapter"])


def test_validate_scope_rejects_unknown_subject():
    with pytest.raises(CatalogError):
        validate_scope(CLASS, "Not A Real Subject", ["whatever"])


def test_validate_scope_rejects_topic_not_in_selected_chapter():
    with pytest.raises(CatalogError):
        validate_scope(CLASS, SUBJECT, [CHAPTER], topics=["Definitely Not A Real Topic"])
