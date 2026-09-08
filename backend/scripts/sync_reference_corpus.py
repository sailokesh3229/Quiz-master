"""Populates Postgres's reference_corpus table from Chroma's own
reference_corpus collection (ingestion/scripts/embed_and_store.py writes
there, not to Postgres at all) — a gap found in production 2026-09-02:
app/explanations.py's _get_style_reference has always queried Postgres for
this table, which turned out to have been completely empty the whole time
(0 rows), a real gap, not by accident: nothing in this codebase before now
ever wrote to it. This script is the missing sync step, safe to re-run any
time ingestion adds a new subject/chapter's exercise entries to Chroma.

A full re-sync (TRUNCATE + re-insert) rather than an incremental upsert:
reference_corpus has no natural unique business key (Chroma's own id,
"<class>|<subject>|<pdf-stem>|<index>", isn't meaningful once re-ingestion
changes chapter content), and this table is a pure derived view of Chroma's
data, not an independent source of truth, so a full resync is simple and
correct.

Usage: .venv/Scripts/python scripts/sync_reference_corpus.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chromadb

from app.db import get_connection

CHROMA_PATH = Path(__file__).resolve().parent.parent.parent / "ingestion" / "chroma_db"


def main() -> None:
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    coll = client.get_collection("reference_corpus")
    res = coll.get(include=["documents", "metadatas"])

    rows = []
    for question, meta in zip(res["documents"], res["metadatas"]):
        answer = meta.get("answer") or None  # embed_and_store.py stores "" for a genuinely-missing answer
        # Pre-existing gap, not introduced here: the Maths book's exercise
        # entries only ever carry topic_number (a "Section N.M" marker),
        # never a real topic NAME — app/explanations.py's style-reference
        # query prefers a same-topic row by comparing its `topic` column
        # against the caller's real topic name, which a section-number
        # string can never match, so that preference silently never fires
        # for this data (harmless: it still falls back to any same-chapter
        # row). Not fixed here — would need capturing real topic names in
        # extract_exercises.py, out of scope for this pass — just mapped
        # through as-is rather than either worsened or silently dropped.
        topic = meta.get("topic_number") or None
        source = meta.get("source") or "ncert_intext"
        rows.append((meta["class"], meta["subject"], meta["chapter"], topic, question, answer, source))

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE reference_corpus")
        cur.executemany(
            """
            INSERT INTO reference_corpus (class, subject, chapter, topic, question, answer, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            rows,
        )
        conn.commit()

    print(f"Synced {len(rows)} reference_corpus rows from Chroma into Postgres.")


if __name__ == "__main__":
    main()
