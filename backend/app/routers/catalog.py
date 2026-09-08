"""Section 3: quiz-selection metadata endpoints (PLAN.md section 3's
'Quiz selection metadata' API surface) — subjects/chapters/topics by real
textbook name (SPEC section 5), plus the valid question-count options for
a given sub-unit count (SPEC 10a's coverage rule)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app import catalog as catalog_module
from app.api_schemas import ChapterOut, QuestionCountOptionsOut, TopicOut
from app.auth import get_current_user_id
from app.scope_resolution import valid_question_counts

router = APIRouter(prefix="/api/catalog", tags=["catalog"], dependencies=[Depends(get_current_user_id)])


@router.get("/subjects", response_model=list[str])
def get_subjects(class_: str):
    return catalog_module.list_subjects(class_)


@router.get("/chapters", response_model=list[ChapterOut])
def get_chapters(class_: str, subject: str):
    chapters = catalog_module.list_chapters(class_, subject)
    if not chapters:
        raise HTTPException(404, f"no chapters found for class {class_!r} subject {subject!r}")
    return [ChapterOut(chapter=c.chapter, chapter_number=c.chapter_number) for c in chapters]


@router.get("/topics", response_model=list[TopicOut])
def get_topics(class_: str, subject: str, chapter: str):
    topics = catalog_module.list_topics(class_, subject, chapter)
    if not topics:
        raise HTTPException(404, f"no topics found for {class_!r}/{subject!r}/{chapter!r}")
    return [TopicOut(topic=t.topic, topic_number=t.topic_number) for t in topics]


@router.get("/question-count-options", response_model=QuestionCountOptionsOut)
def get_question_count_options(sub_unit_count: int, coverage_required: bool):
    return QuestionCountOptionsOut(options=valid_question_counts(sub_unit_count, coverage_required))
