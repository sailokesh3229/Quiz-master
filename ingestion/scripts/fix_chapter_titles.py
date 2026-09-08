"""Re-extracts chapter number/title for the whole corpus with the fixed
extractor (schema.extract_chapter_meta), including a cross-chapter pass:
a title that wins for 2+ different chapters in the same class+subject is
almost certainly the *book's* running header (real in Social Science,
where every page carries the book title, not a chapter-specific one),
not a real per-chapter title, so those chapters are re-extracted with it
excluded from the tier-1 vote.

Prints the final (class, subject, chapter_file, number, title) table.
Does not touch Chroma — see patch_chapter_metadata.py for applying this
to the already-ingested corpus.

Usage: .venv/Scripts/python scripts/fix_chapter_titles.py
"""

import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pymupdf

from schema import extract_chapter_meta

ROOT = Path(__file__).resolve().parent.parent.parent


def chapter_files(subj_dir: Path) -> list[Path]:
    return sorted(subj_dir.glob("chapter *.pdf"), key=lambda p: int(re.search(r"\d+", p.stem).group()))


def fix_subject(cls: str, subj: str, files: list[Path]) -> dict[Path, dict]:
    docs = {f: pymupdf.open(str(f)) for f in files}
    first_pass = {f: extract_chapter_meta(doc) for f, doc in docs.items()}

    # Grouped case-insensitively: the same recurring book/feature label
    # can render with different casing across chapters — a color-banner
    # ALL-CAPS source converts through this pipeline's headline-case
    # step ("Notes for the Teacher"), while a chapter whose source for
    # that same label is already mixed-case keeps it as printed
    # ("Notes For The Teacher", every word capitalized) — found in
    # production, and enough to make an exact-string comparison miss
    # that they're the same recurring label at all.
    title_to_files: dict[str, set[Path]] = defaultdict(set)
    for f, meta in first_pass.items():
        if meta["title"] != "Unknown":
            title_to_files[meta["title"].lower()].add(f)
    book_level_norms = {t for t, fs in title_to_files.items() if len(fs) >= 2}

    if not book_level_norms:
        for doc in docs.values():
            doc.close()
        return first_pass

    # exclude_titles must contain the exact strings extract_chapter_meta
    # will compare against internally, so every distinct casing variant
    # observed for a book-level norm needs to be included, not just one.
    book_level_titles = {meta["title"] for meta in first_pass.values() if meta["title"].lower() in book_level_norms}

    final = dict(first_pass)
    for f, meta in first_pass.items():
        if meta["title"].lower() in book_level_norms:
            final[f] = extract_chapter_meta(docs[f], exclude_titles=book_level_titles)

    for doc in docs.values():
        doc.close()
    return final


def main():
    # This environment kills a single long-running background process
    # after ~200-240s regardless of requested timeout (same constraint
    # PARSING_EXCEPTIONS.md's tooling note already hit during the
    # original corpus ingestion) — write incrementally per subject so a
    # kill mid-run doesn't lose already-computed results, and accept an
    # optional class-number filter so the remaining classes can be run
    # in a fresh invocation instead of restarting from scratch.
    only_classes = set(sys.argv[1:]) or None
    out_path = Path(__file__).parent.parent / "output" / "chapter_titles_final.tsv"
    done_keys = set()
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                done_keys.add((parts[0], parts[1], parts[2]))

    with open(out_path, "a", encoding="utf-8") as out:
        for cls_dir in sorted(ROOT.glob("class *")):
            cls = cls_dir.name.replace("class ", "")
            if only_classes and cls not in only_classes:
                continue
            for subj_dir in sorted(cls_dir.iterdir()):
                if not subj_dir.is_dir():
                    continue
                files = chapter_files(subj_dir)
                if not files:
                    continue
                if all((cls, subj_dir.name, f.stem) in done_keys for f in files):
                    print(f"{cls}/{subj_dir.name}: already done, skipping", file=sys.stderr, flush=True)
                    continue
                final = fix_subject(cls, subj_dir.name, files)
                for f in files:
                    meta = final[f]
                    out.write("\t".join(str(x) for x in (cls, subj_dir.name, f.stem, meta["number"], meta["title"])) + "\n")
                out.flush()
                print(f"{cls}/{subj_dir.name}: {len(files)} chapters done", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
