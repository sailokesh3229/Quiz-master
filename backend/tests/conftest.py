import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.db import get_connection


@pytest.fixture(scope="session")
def embed_model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer("all-MiniLM-L6-v2")


@pytest.fixture(scope="session")
def sample_chunk_text():
    """A real Section 1 chunk (class 10 Science, topic 1.1 Chemical
    Equations) — grounding content for prompt-building tests."""
    import chromadb

    chroma_path = Path(__file__).resolve().parent.parent.parent / "ingestion" / "chroma_db"
    client = chromadb.PersistentClient(path=str(chroma_path))
    coll = client.get_collection("chunks")
    res = coll.get(
        where={"$and": [{"class": "10"}, {"subject": "Science"}, {"topic_number": "1.1"}]},
        include=["documents"],
        limit=1,
    )
    return res["documents"][0]


@pytest.fixture
def db_conn():
    """A connection with an open transaction that's always rolled back
    after the test — seeded test data never touches real bank data, and
    tests never need manual cleanup."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()
