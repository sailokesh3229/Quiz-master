"""Section 3: FastAPI dependencies shared across routers — DB connection
with request-scoped commit/rollback, the embedding model singleton, and
the LLM providers. Service-layer functions (quiz_service.py, explanations.py,
Section 2's own modules) never call conn.commit()/rollback() themselves —
that's a deliberate convention (see quiz_service.py's history) so the same
functions work correctly both here (commit on a successful request) and in
tests (tests/conftest.py's db_conn fixture, which only ever rolls back)."""

from __future__ import annotations

from functools import lru_cache

from app.db import get_connection
from app.llm_client import Provider, default_fallback, default_primary


def get_db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@lru_cache(maxsize=1)
def _embed_model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer("all-MiniLM-L6-v2")


def get_embed_model():
    return _embed_model()


@lru_cache(maxsize=1)
def _primary_provider() -> Provider:
    return default_primary()


@lru_cache(maxsize=1)
def _fallback_provider() -> Provider:
    return default_fallback()


def get_primary_provider() -> Provider:
    return _primary_provider()


def get_fallback_provider() -> Provider:
    return _fallback_provider()
