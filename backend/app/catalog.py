"""Section 3: quiz-selection metadata endpoints' data layer (PLAN.md
section 3, API surface 'Quiz selection metadata'). Reads Section 1's
Chroma `chunks` collection to answer: which subjects exist for a class,
which chapters exist for a class+subject, which topics exist for a
chapter, and how many sub-units a chapter/subject has (for SPEC 10a's
coverage rule at the question-count step).

Chapters and topics are always the real textbook names (SPEC section 5) —
`chapter_number`/`topic_number` are internal-only, used here solely to
order the lists sensibly, never as the identity or the display label.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import chromadb

_DEFAULT_CHROMA_PATH = Path(__file__).resolve().parent.parent.parent / "ingestion" / "chroma_db"
CHROMA_PATH = Path(os.environ.get("CHROMA_DB_PATH", str(_DEFAULT_CHROMA_PATH)))


@lru_cache(maxsize=1)
def _client() -> chromadb.ClientAPI:
    return chromadb.PersistentClient(path=str(CHROMA_PATH))


def _collection():
    return _client().get_collection("chunks")


def _natural_sort_key(number: str | int | None):
    """topic_number/chapter_number are stored as a bare int (chapter_number),
    a dotted string like '1.1'/'2.10' (topic_number), or '' (task 11
    finding: chapter_number can be missing, and Chroma metadata can't hold
    None) — sort numerically by dotted segments so '1.10' doesn't sort
    before '1.2', with missing numbers pushed to the end rather than
    crashing the sort."""
    if number in (None, ""):
        return (1, [])
    return (0, [int(p) if p.isdigit() else p for p in re.split(r"\D+", str(number)) if p != ""])


@dataclass
class ChapterInfo:
    chapter: str
    chapter_number: str | None


@dataclass
class TopicInfo:
    topic: str
    topic_number: str | None


def _all_metadatas(where: dict | None) -> list[dict]:
    # Chroma's .get() rejects an empty {} where-filter ("expected where to
    # have exactly one operator") — omit the kwarg entirely to mean "no
    # filter, return everything".
    kwargs = {"where": where} if where else {}
    res = _collection().get(include=["metadatas"], **kwargs)
    return res["metadatas"]


def list_subjects(class_: str) -> list[str]:
    metas = _all_metadatas({"class": class_})
    return sorted({m["subject"] for m in metas})


def list_chapters(class_: str, subject: str) -> list[ChapterInfo]:
    metas = _all_metadatas({"$and": [{"class": class_}, {"subject": subject}]})
    by_chapter: dict[str, str | None] = {}
    for m in metas:
        raw = m.get("chapter_number")
        by_chapter.setdefault(m["chapter"], str(raw) if raw not in (None, "") else None)
    chapters = [ChapterInfo(chapter=c, chapter_number=n) for c, n in by_chapter.items()]
    chapters.sort(key=lambda c: (_natural_sort_key(c.chapter_number), c.chapter))
    return chapters


def list_topics(class_: str, subject: str, chapter: str) -> list[TopicInfo]:
    metas = _all_metadatas({"$and": [{"class": class_}, {"subject": subject}, {"chapter": chapter}]})
    by_topic: dict[str, str | None] = {}
    for m in metas:
        raw = m.get("topic_number")
        by_topic.setdefault(m["topic"], str(raw) if raw not in (None, "") else None)
    topics = [TopicInfo(topic=t, topic_number=n) for t, n in by_topic.items()]
    topics.sort(key=lambda t: (_natural_sort_key(t.topic_number), t.topic))
    return topics


def count_topics_in_chapter(class_: str, subject: str, chapter: str) -> int:
    return len(list_topics(class_, subject, chapter))


def count_chapters_in_subject(class_: str, subject: str) -> int:
    return len(list_chapters(class_, subject))


class CatalogError(ValueError):
    """Raised when a requested class/subject/chapter/topic doesn't exist
    in Section 1's data — a scope the app should never let a user reach
    (SPEC section 14: 'a user should never be asked a question outside the
    scope they selected'), but re-checked here rather than trusted blindly
    from client input."""


def validate_scope(class_: str, subject: str, chapters: list[str], topics: list[str] | None = None) -> None:
    available_chapters = {c.chapter for c in list_chapters(class_, subject)}
    if not available_chapters:
        raise CatalogError(f"no content for class {class_!r} subject {subject!r}")
    unknown_chapters = set(chapters) - available_chapters
    if unknown_chapters:
        raise CatalogError(f"unknown chapter(s) for class {class_!r} subject {subject!r}: {unknown_chapters}")
    if topics:
        available_topics: set[str] = set()
        for ch in chapters:
            available_topics |= {t.topic for t in list_topics(class_, subject, ch)}
        unknown_topics = set(topics) - available_topics
        if unknown_topics:
            raise CatalogError(f"unknown topic(s) for the selected chapter(s): {unknown_topics}")
