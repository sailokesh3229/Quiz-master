"""Section 3: profile endpoints (SPEC section 4 — a stored default class,
the only app-specific per-user state Clerk doesn't already hold)."""

from __future__ import annotations

import psycopg
from fastapi import APIRouter, Depends

from app.api_schemas import ProfileOut, ProfileUpdate
from app.auth import get_current_user_id
from app.deps import get_db

router = APIRouter(prefix="/api/profile", tags=["profile"])


@router.get("", response_model=ProfileOut)
def get_profile(user_id: str = Depends(get_current_user_id), conn: psycopg.Connection = Depends(get_db)):
    with conn.cursor() as cur:
        cur.execute("SELECT default_class FROM profiles WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
    return ProfileOut(user_id=user_id, default_class=row["default_class"] if row else None)


@router.put("", response_model=ProfileOut)
def update_profile(
    body: ProfileUpdate, user_id: str = Depends(get_current_user_id), conn: psycopg.Connection = Depends(get_db)
):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO profiles (user_id, default_class) VALUES (%s, %s)
            ON CONFLICT (user_id) DO UPDATE SET default_class = EXCLUDED.default_class, updated_at = now()
            """,
            (user_id, body.default_class),
        )
    return ProfileOut(user_id=user_id, default_class=body.default_class)
