"""Applies the corrected chapter number/title from
output/chapter_titles_final.tsv (fix_chapter_titles.py's output) to every
already-ingested chunk in Chroma, matched by the reliable `source_file`
field (the PDF filename stem) rather than the old, possibly-wrong
`chapter`/`chapter_number` values. Only those two fields are touched —
embeddings, document text, topic, and every other metadata field are
left exactly as they are.

Usage: .venv/Scripts/python scripts/patch_chapter_metadata.py [--dry-run]
"""

import sys
from pathlib import Path

import chromadb

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

CHROMA_PATH = Path(__file__).resolve().parent.parent / "chroma_db"
TSV_PATH = Path(__file__).resolve().parent.parent / "output" / "chapter_titles_final.tsv"

SUBJECT_DIR_TO_METADATA = {
    "maths": "Maths",
    "science": "Science",
    "social science": "Social Science",
    "physics": "Physics",
    "chemistry": "Chemistry",
    "biology": "Biology",
}


def main():
    dry_run = "--dry-run" in sys.argv
    # Scope to (class, subject_dir) pairs verified accurate by manual
    # review — the extractor is not yet reliable across the board (some
    # Science/Social Science books use page layouts, book-level running
    # headers, or duplicate-offset stylized text this heuristic can't
    # resolve; see PARSING_EXCEPTIONS.md item 3). Pass --all to bypass
    # this and patch everything the TSV contains.
    only_scope = None
    if "--all" not in sys.argv:
        only_scope = {(c, "maths") for c in ("6", "7", "8", "9", "10")} | {("10", "science")}

    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    coll = client.get_collection("chunks")

    rows = []
    for line in TSV_PATH.read_text(encoding="utf-8").splitlines():
        cls, subj_dir, stem, number, title = line.split("\t", 4)
        if only_scope is not None and (cls, subj_dir) not in only_scope:
            continue
        # Chroma metadata can't store None — the existing corpus convention
        # (confirmed against already-ingested chunks) is "" for "no chapter
        # number", and an int otherwise.
        rows.append((cls, SUBJECT_DIR_TO_METADATA[subj_dir], stem, "" if number == "None" else int(number), title))

    total_updated = 0
    total_chunks = 0
    for cls, subject, stem, number, title in rows:
        res = coll.get(
            where={"$and": [{"class": cls}, {"subject": subject}, {"source_file": stem}]},
            include=["metadatas"],
        )
        ids = res["ids"]
        metas = res["metadatas"]
        if not ids:
            print(f"WARNING: no chunks found for {cls}/{subject}/{stem}", file=sys.stderr)
            continue
        changed = [m for m in metas if m.get("chapter") != title or m.get("chapter_number") != number]
        total_chunks += len(ids)
        if changed:
            total_updated += len(ids)
            print(f"{cls}/{subject}/{stem}: {len(ids)} chunks -> chapter={title!r} number={number!r}")
        if not dry_run and changed:
            new_metas = []
            for m in metas:
                m = dict(m)
                m["chapter"] = title
                m["chapter_number"] = number
                new_metas.append(m)
            coll.update(ids=ids, metadatas=new_metas)

    print(f"\n{'[dry run] ' if dry_run else ''}{total_updated} chunks updated out of {total_chunks} total scanned")


if __name__ == "__main__":
    main()
