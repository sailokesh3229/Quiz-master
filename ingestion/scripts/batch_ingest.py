"""Tasks 10-11: scale the pipeline across chapters. Given a folder
structure of `class N/subject/chapter M.pdf`, run embed_and_store on every
chapter PDF found (skipping "CBSE question papers" subfolders — those are
a separate future v2 reference corpus per SPEC.md section 13, not part of
this pipeline), logging progress and any per-chapter failure so nothing
fails silently.

Task 11 finding: this environment kills a single long-running background
process after roughly 200-240s regardless of the requested timeout, so a
one-shot run across the full ~200-chapter corpus isn't viable. --time-
budget makes each invocation stop cleanly (finish the current chapter,
then return) once elapsed time crosses the budget, instead of risking an
external kill mid-write; --skip-existing lets repeated invocations resume
without redoing chapters a prior run already stored.

Usage:
  .venv/Scripts/python scripts/batch_ingest.py <root-dir> [--no-tables] [--only-class N] [--only-subject S] [--time-budget SECONDS] [--skip-existing]
"""

import sys
import time
import traceback
from pathlib import Path

from embed_and_store import embed_and_store_pdf, get_client

SUBJECT_NAME_MAP = {"maths": "Maths", "science": "Science", "social science": "Social Science"}


def find_chapter_pdfs(root: Path, only_class: str | None, only_subject: str | None) -> list[tuple[Path, str, str]]:
    """Return [(pdf_path, class_, subject)] for every "chapter N.pdf" under
    root/class */subject/, in class -> subject -> chapter-number order."""
    found = []
    for class_dir in sorted(root.glob("class *")):
        class_num = class_dir.name.replace("class ", "").strip()
        if only_class and class_num != only_class:
            continue
        for subject_dir in sorted(class_dir.iterdir()):
            if not subject_dir.is_dir():
                continue
            subject = SUBJECT_NAME_MAP.get(subject_dir.name.lower(), subject_dir.name.title())
            if only_subject and subject.lower() != only_subject.lower():
                continue
            chapters = sorted(
                subject_dir.glob("chapter *.pdf"),
                key=lambda p: int("".join(ch for ch in p.stem if ch.isdigit()) or 0),
            )
            for pdf in chapters:
                found.append((pdf, class_num, subject))
    return found


def already_ingested_source_files(class_: str, subject: str) -> set[str]:
    """Resume/skip key. chapter_number is only best-effort (task 11
    finding: often None for books without a clean cover-page digit, e.g.
    several Social Science chapters) — source_file (the PDF's own stem,
    always present) is the reliable one."""
    coll = get_client().get_or_create_collection("chunks")
    res = coll.get(where={"$and": [{"class": class_}, {"subject": subject}]}, include=["metadatas"])
    return {m["source_file"] for m in res["metadatas"] if m.get("source_file")}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    raw = sys.argv[1:]
    skip_tables = "--no-tables" in raw
    skip_existing = "--skip-existing" in raw

    only_class = None
    only_subject = None
    time_budget = None
    start_index = 0
    positional = []
    i = 0
    while i < len(raw):
        a = raw[i]
        if a in ("--no-tables", "--skip-existing"):
            i += 1
        elif a == "--only-class":
            only_class = raw[i + 1]
            i += 2
        elif a == "--only-subject":
            only_subject = raw[i + 1]
            i += 2
        elif a == "--time-budget":
            time_budget = float(raw[i + 1])
            i += 2
        elif a == "--start-index":
            # O(1) resume, no DB lookups — use once a prior run's "Time
            # budget reached after N/total" tells you where it stopped;
            # skip-existing's per-item DB check gets slower to churn
            # through as more of the corpus is already done (task 11
            # finding), this sidesteps that entirely.
            start_index = int(raw[i + 1])
            i += 2
        else:
            positional.append(a)
            i += 1

    if len(positional) != 1:
        print(
            "Usage: batch_ingest.py <root-dir> [--no-tables] [--only-class N] [--only-subject S] "
            "[--time-budget SECONDS] [--skip-existing] [--start-index N]"
        )
        sys.exit(1)

    root = Path(positional[0])
    targets = find_chapter_pdfs(root, only_class, only_subject)
    print(
        f"{len(targets)} chapter PDFs found (skip_tables={skip_tables}, time_budget={time_budget}, start_index={start_index})\n",
        flush=True,
    )

    ok, skipped, failed = 0, 0, []
    t_start = time.time()
    existing_cache: dict[tuple, set] = {}
    for i, (pdf_path, class_, subject) in enumerate(targets, 1):
        if i <= start_index:
            continue
        if time_budget is not None and time.time() - t_start > time_budget:
            print(f"\nTime budget reached after {i-1}/{len(targets)} — stopping cleanly, re-run to resume.", flush=True)
            break

        if skip_existing:
            key = (class_, subject)
            if key not in existing_cache:
                existing_cache[key] = already_ingested_source_files(class_, subject)
            if pdf_path.stem in existing_cache[key]:
                skipped += 1
                continue

        t0 = time.time()
        try:
            result = embed_and_store_pdf(pdf_path, class_, subject, skip_tables=skip_tables)
            elapsed = time.time() - t0
            print(
                f"[{i}/{len(targets)}] OK   class={class_:>2} {subject:<15} {pdf_path.name:<15} "
                f"{result['chunks']:>3} chunks {result['corpus']:>3} corpus  {elapsed:>6.1f}s",
                flush=True,
            )
            ok += 1
        except Exception as e:
            elapsed = time.time() - t0
            print(
                f"[{i}/{len(targets)}] FAIL class={class_:>2} {subject:<15} {pdf_path.name:<15} "
                f"{elapsed:>6.1f}s  {type(e).__name__}: {e}",
                flush=True,
            )
            failed.append((str(pdf_path), f"{type(e).__name__}: {e}"))
            traceback.print_exc(file=sys.stderr)

    total = time.time() - t_start
    print(f"\n{ok}/{len(targets)} succeeded, {skipped} skipped (already ingested), {len(failed)} failed, total {total/60:.1f} min")
    if failed:
        print("\nFailures:")
        for path, err in failed:
            print(f"  {path}: {err}")


if __name__ == "__main__":
    main()
