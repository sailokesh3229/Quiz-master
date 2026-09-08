"""Section 3: Pydantic request/response models for the HTTP API (PLAN.md
section 3, section 3 'Backend API surface'). Kept separate from the
question-generation schemas in prompts.py — those validate LLM output,
these validate/shape the wire format between frontend and backend."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

QuestionType = Literal["MCQ", "fill_in_blank", "matching"]
Difficulty = Literal["easy", "medium", "hard"]
ScopeType = Literal["single_topic", "multi_topic", "all_topics_in_chapter", "all_chapters_in_subject"]


# ---- Profile ----

class ProfileOut(BaseModel):
    user_id: str
    default_class: str | None


class ProfileUpdate(BaseModel):
    default_class: str


class DailyUsageOut(BaseModel):
    used_today: int
    limit: int


# ---- Catalog ----

class ChapterOut(BaseModel):
    chapter: str
    chapter_number: str | None


class TopicOut(BaseModel):
    topic: str
    topic_number: str | None


class QuestionCountOptionsOut(BaseModel):
    options: list[int]


# ---- Quiz creation ----

class CreateQuizIn(BaseModel):
    class_: str = Field(alias="class")
    subject: str
    scope_type: ScopeType
    chapters: list[str] = Field(min_length=1)
    topics: list[str] | None = None
    difficulty: Difficulty
    question_count: int
    question_types: list[QuestionType] = Field(min_length=1)
    allow_partial: bool = False

    model_config = {"populate_by_name": True}


class DeliveredQuestionOut(BaseModel):
    question_id: str
    question_type: QuestionType
    topic: str
    chapter: str
    difficulty: Difficulty
    text: str
    payload: dict  # public view only — see quiz_service._public_payload


class CreateQuizOut(BaseModel):
    questions: list[DeliveredQuestionOut]
    requested_count: int
    delivered_count: int
    partial: bool


# ---- Quiz submission ----

class ScopeEcho(BaseModel):
    scope_type: ScopeType
    sub_units: list[str]


class SubmitQuizIn(BaseModel):
    class_: str = Field(alias="class")
    subject: str
    scope: ScopeEcho
    difficulty: Difficulty
    is_requiz: bool
    answers: dict[str, object]  # question_id -> raw answer (int for MCQ, str for fill-blank, dict for matching)

    model_config = {"populate_by_name": True}


class PerQuestionResultOut(BaseModel):
    question_id: str
    question_type: QuestionType
    topic: str
    is_correct: bool
    row_results: list[bool] | None = None


class SubmitQuizOut(BaseModel):
    attempt_id: str | None
    score_correct: int
    score_total: int
    options: list[str]
    per_question: list[PerQuestionResultOut]


# ---- Solutions ----

class SolutionsFromAnswersIn(BaseModel):
    answers: dict[str, object]


class SolutionEntryOut(BaseModel):
    question_id: str
    question_type: QuestionType
    topic: str
    text: str
    correct_payload: dict
    user_answer: object
    is_correct: bool
    row_results: list[bool] | None = None
    explanation: str


class SolutionsOut(BaseModel):
    solutions: list[SolutionEntryOut]


# ---- Performance dashboard ----

class AccuracyBucketOut(BaseModel):
    label: str
    correct: int
    total: int
    accuracy: float | None


class WeeklyAttemptsOut(BaseModel):
    week_start: str
    correct: int
    total: int
    attempt_count: int


class DashboardOut(BaseModel):
    overall: AccuracyBucketOut
    by_subject: list[AccuracyBucketOut]
    by_chapter: list[AccuracyBucketOut]
    by_question_type: list[AccuracyBucketOut]
    by_difficulty: list[AccuracyBucketOut]
    attempts_over_time: list[WeeklyAttemptsOut]
    recent_attempts: list[dict]
