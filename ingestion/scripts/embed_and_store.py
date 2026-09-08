"""Tasks 7-8: embed each chunk's text and store chunk + metadata + embedding
in a Chroma collection with metadata fields as filterable attributes.

Embeds and stores both grounding chunks (schema.build_chunks) and the
separate exercise/answer reference corpus (schema.build_reference_corpus_records)
in two distinct collections, matching PLAN.md section 4's "separate table,
not part of the chunk record" split.

Usage:
  .venv/Scripts/python scripts/embed_and_store.py <path-to-pdf> <class> <subject> [--no-tables]
  .venv/Scripts/python scripts/embed_and_store.py --subject-dir "<dir>" <class> <subject> [--no-tables]
"""

import sys
import time
from pathlib import Path

import chromadb
from sentence_transformers import SentenceTransformer

from schema import build_chunks, build_reference_corpus_records

DB_DIR = Path(__file__).resolve().parent.parent / "chroma_db"
MODEL_NAME = "all-MiniLM-L6-v2"

_model = None


def get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def get_client() -> chromadb.ClientAPI:
    DB_DIR.mkdir(exist_ok=True)
    return chromadb.PersistentClient(path=str(DB_DIR))


def _sanitize_metadata(d: dict) -> dict:
    """Chroma metadata values must be str/int/float/bool/None — flatten
    the list-valued fields (images, figure_dependent_spans) to JSON text
    so they survive round-trip through metadata filtering."""
    import json as _json

    out = {}
    for k, v in d.items():
        if k in ("text", "embedding", "question", "answer"):
            continue
        if isinstance(v, (list, dict)):
            out[k] = _json.dumps(v, ensure_ascii=False)
        elif v is None:
            out[k] = ""
        else:
            out[k] = v
    return out


def embed_and_store_pdf(pdf_path: Path, class_: str, subject: str, skip_tables: bool = False) -> dict:
    model = get_model()
    client = get_client()
    chunks_coll = client.get_or_create_collection("chunks")
    corpus_coll = client.get_or_create_collection("reference_corpus")

    chunks = build_chunks(pdf_path, class_, subject, skip_tables=skip_tables)
    corpus = build_reference_corpus_records(pdf_path, class_, subject)

    if chunks:
        texts = [c["text"] for c in chunks]
        embeddings = model.encode(texts, show_progress_bar=False).tolist()
        chunks_coll.upsert(
            ids=[c["chunk_id"] for c in chunks],
            documents=texts,
            embeddings=embeddings,
            metadatas=[_sanitize_metadata(c) for c in chunks],
        )

    if corpus:
        q_texts = [e["question"] for e in corpus]
        q_embeddings = model.encode(q_texts, show_progress_bar=False).tolist()
        # task 11 finding: (topic_number, question_number) isn't always
        # unique — some books restate a "Section N.M" marker more than
        # once, or reuse question numbers across it — so a running index
        # is the only reliably unique key, same fix as schema.py's
        # topic_ordinal for the chunks collection.
        ids = [f"{class_}|{subject}|{pdf_path.stem}|{i}" for i in range(len(corpus))]
        corpus_coll.upsert(
            ids=ids,
            documents=q_texts,
            embeddings=q_embeddings,
            # answer is None for a source='ncert_activity' entry (SPEC.md
            # section 6: a question-wording style reference with no printed
            # answer at all) — Chroma metadata values must be str/int/float/
            # bool, same reasoning _sanitize_metadata already applies to
            # every other field, just kept out of that helper (like
            # "question") since this is the one place "answer" is handled.
            metadatas=[_sanitize_metadata(e) | {"answer": e["answer"] or ""} for e in corpus],
        )

    return {"chunks": len(chunks), "corpus": len(corpus)}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    skip_tables = "--no-tables" in sys.argv

    if len(args) != 3:
        print("Usage: embed_and_store.py <path-to-pdf> <class> <subject> [--no-tables]")
        sys.exit(1)

    pdf_path, class_, subject = Path(args[0]), args[1], args[2]

    t0 = time.time()
    result = embed_and_store_pdf(pdf_path, class_, subject, skip_tables=skip_tables)
    print(f"{pdf_path.name}: {result['chunks']} chunks, {result['corpus']} corpus entries stored in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
