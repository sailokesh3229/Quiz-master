"""Connection helper for the quizmaster PostgreSQL + pgvector database
(PLAN.md section 4a). Local dev instance: a self-contained conda-forge
postgresql+pgvector install (see backend/README.md), running on port 5433
so it doesn't collide with any other local Postgres instance.
"""

import os

import psycopg
from psycopg.rows import dict_row

from app.config import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres@localhost:5433/quizmaster")


def get_connection() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)
