"""Section 3: FastAPI dependencies shared across routers — DB connection
with request-scoped commit/rollback, the embedding model singleton, and
the LLM providers. Service-layer functions (quiz_service.py, explanations.py,
Section 2's own modules) never call conn.commit()/rollback() themselves —
that's a deliberate convention (see quiz_service.py's history) so the same
functions work correctly both here (commit on a successful request) and in
tests (tests/conftest.py's db_conn fixture, which only ever rolls back)."""

from __future__ import annotations

from functools import lru_cache
from threading import Lock

from app.db import get_connection
from app.llm_client import Provider, default_fallback, default_primary
from app.diagnostics import stage

_model_lock = Lock()


def get_db():
    with stage("database.connect"):
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
    with stage("embedding.import"):
        from app.embeddings import MiniLMEncoder
    with stage("embedding.load"):
        return MiniLMEncoder()


def get_embed_model():
    # lru_cache alone can initialize twice when two requests arrive together.
    with _model_lock:
        return _embed_model()


@lru_cache(maxsize=1)
def _primary_provider() -> Provider:
    return default_primary()


@lru_cache(maxsize=1)
def _fallback_provider() -> Provider | None:
    return default_fallback()


def get_primary_provider() -> Provider:
    return _primary_provider()


def get_fallback_provider() -> Provider | None:
    return _fallback_provider()
