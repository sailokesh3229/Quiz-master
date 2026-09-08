"""Section 3: performance dashboard endpoint (SPEC section 11/11a)."""

from __future__ import annotations

import psycopg
from fastapi import APIRouter, Depends

from app.api_schemas import AccuracyBucketOut, DashboardOut, WeeklyAttemptsOut
from app.auth import get_current_user_id
from app.deps import get_db
from app.performance_service import get_dashboard

router = APIRouter(prefix="/api/performance", tags=["performance"])


def _bucket_out(b) -> AccuracyBucketOut:
    return AccuracyBucketOut(label=b.label, correct=b.correct, total=b.total, accuracy=b.accuracy)


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(user_id: str = Depends(get_current_user_id), conn: psycopg.Connection = Depends(get_db)):
    data = get_dashboard(conn, user_id)
    return DashboardOut(
        overall=_bucket_out(data.overall),
        by_subject=[_bucket_out(b) for b in data.by_subject],
        by_chapter=[_bucket_out(b) for b in data.by_chapter],
        by_question_type=[_bucket_out(b) for b in data.by_question_type],
        by_difficulty=[_bucket_out(b) for b in data.by_difficulty],
        attempts_over_time=[
            WeeklyAttemptsOut(week_start=w.week_start, correct=w.correct, total=w.total, attempt_count=w.attempt_count)
            for w in data.attempts_over_time
        ],
        recent_attempts=data.recent_attempts,
    )
