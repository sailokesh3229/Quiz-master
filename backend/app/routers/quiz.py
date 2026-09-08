"""Section 3: quiz lifecycle endpoints (PLAN.md section 3's 'Quiz
lifecycle' API surface) — create, submit, and solutions. 'Requiz with the
same questions' and 'start a new quiz on the same topic' (SPEC section 5
step 11) don't need dedicated endpoints: the frontend already holds the
just-taken question set in memory, so a requiz just re-submits it
(is_requiz=true) and a new-quiz-same-topic just calls create again."""

from __future__ import annotations

import psycopg
from fastapi import APIRouter, Depends, HTTPException

from app.api_schemas import (
    CreateQuizIn,
    CreateQuizOut,
    DailyUsageOut,
    DeliveredQuestionOut,
    PerQuestionResultOut,
    SolutionEntryOut,
    SolutionsFromAnswersIn,
    SolutionsOut,
    SubmitQuizIn,
    SubmitQuizOut,
)
from app.auth import get_current_user_id
from app.deps import get_db, get_embed_model, get_fallback_provider, get_primary_provider
from app.quiz_service import (
    CreateQuizRequest,
    DailyLimitExceeded,
    QuizCreationError,
    SubmitQuizRequest,
    check_and_log_daily_limit,
    create_quiz,
    get_daily_usage,
    solutions_from_answers,
    solutions_from_attempt,
    submit_quiz_request,
)

router = APIRouter(prefix="/api/quiz", tags=["quiz"], dependencies=[Depends(get_current_user_id)])


@router.get("/usage", response_model=DailyUsageOut)
def get_usage(user_id: str = Depends(get_current_user_id), conn: psycopg.Connection = Depends(get_db)):
    used, limit = get_daily_usage(conn, user_id)
    return DailyUsageOut(used_today=used, limit=limit)


@router.post("/create", response_model=CreateQuizOut)
def post_create_quiz(
    body: CreateQuizIn,
    user_id: str = Depends(get_current_user_id),
    conn: psycopg.Connection = Depends(get_db),
    embed_model=Depends(get_embed_model),
    primary=Depends(get_primary_provider),
    fallback=Depends(get_fallback_provider),
):
    if not body.allow_partial:
        try:
            check_and_log_daily_limit(conn, user_id)
        except DailyLimitExceeded as e:
            raise HTTPException(429, str(e)) from e

    request = CreateQuizRequest(
        user_id=user_id, class_=body.class_, subject=body.subject, scope_type=body.scope_type,
        chapters=body.chapters, topics=body.topics, difficulty=body.difficulty,
        question_count=body.question_count, question_types=body.question_types, allow_partial=body.allow_partial,
    )
    try:
        result = create_quiz(conn, request, primary, fallback, embed_model)
    except QuizCreationError as e:
        raise HTTPException(503, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    return CreateQuizOut(
        questions=[
            DeliveredQuestionOut(
                question_id=q.question_id, question_type=q.question_type, topic=q.topic, chapter=q.chapter,
                difficulty=q.difficulty, text=q.text, payload=q.public_payload,
            )
            for q in result.questions
        ],
        requested_count=result.requested_count,
        delivered_count=len(result.questions),
        partial=result.partial,
    )


@router.post("/submit", response_model=SubmitQuizOut)
def post_submit_quiz(
    body: SubmitQuizIn,
    user_id: str = Depends(get_current_user_id),
    conn: psycopg.Connection = Depends(get_db),
    embed_model=Depends(get_embed_model),
):
    request = SubmitQuizRequest(
        user_id=user_id, class_=body.class_, subject=body.subject,
        scope={"scope_type": body.scope.scope_type, "sub_units": body.scope.sub_units},
        difficulty=body.difficulty, is_requiz=body.is_requiz, answers=body.answers,
    )
    try:
        result = submit_quiz_request(conn, request, embed_model)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    return SubmitQuizOut(
        attempt_id=result.attempt_id, score_correct=result.score_correct, score_total=result.score_total,
        options=list(result.options),
        per_question=[PerQuestionResultOut(**p) for p in result.per_question],
    )


@router.get("/solutions/{attempt_id}", response_model=SolutionsOut)
def get_solutions_by_attempt(
    attempt_id: str,
    user_id: str = Depends(get_current_user_id),
    conn: psycopg.Connection = Depends(get_db),
    primary=Depends(get_primary_provider),
    fallback=Depends(get_fallback_provider),
):
    with conn.cursor() as cur:
        cur.execute("SELECT user_id FROM quiz_attempts WHERE attempt_id = %s", (attempt_id,))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(404, f"unknown attempt_id: {attempt_id}")
    if row["user_id"] != user_id:
        raise HTTPException(403, "this attempt does not belong to the current user")

    try:
        entries = solutions_from_attempt(conn, attempt_id, primary, fallback)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return SolutionsOut(solutions=[SolutionEntryOut(**e) for e in entries])


@router.post("/solutions", response_model=SolutionsOut)
def post_solutions_from_answers(
    body: SolutionsFromAnswersIn,
    conn: psycopg.Connection = Depends(get_db),
    embed_model=Depends(get_embed_model),
    primary=Depends(get_primary_provider),
    fallback=Depends(get_fallback_provider),
):
    """For a requiz replay (SPEC section 11): nothing was persisted, so
    the client sends back the question_ids/answers it already holds from
    just taking the quiz."""
    entries = solutions_from_answers(conn, body.answers, primary, fallback, embed_model)
    return SolutionsOut(solutions=[SolutionEntryOut(**e) for e in entries])
