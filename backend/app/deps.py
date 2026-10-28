"""Section 3: FastAPI dependencies shared across routers — DB connection
with request-scoped commit/rollback, the embedding model singleton, and
the LLM providers. Service-layer functions (quiz_service.py, explanations.py,
Section 2's own modules) never call conn.commit()/rollback() themselves —
that's a deliberate convention (see quiz_service.py's history) so the same
functions work correctly both here (commit on a successful request) and in
tests (tests/conftest.py's db_conn fixture, which only ever rolls back)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from threading import Lock
import os

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
        # Prevent optional frameworks and excessive CPU thread pools on small hosts.
        os.environ.setdefault("USE_TF", "0")
        os.environ.setdefault("USE_FLAX", "0")
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        import torch
        torch.set_num_threads(1)
        from sentence_transformers import SentenceTransformer

    model_path = Path(__file__).resolve().parent.parent / ".models" / "all-MiniLM-L6-v2"
    with stage("embedding.load"):
        # Render's build saves this complete model. Runtime needs no HF requests.
        return SentenceTransformer(str(model_path) if model_path.exists() else "all-MiniLM-L6-v2",
                                   local_files_only=True, device="cpu")


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
