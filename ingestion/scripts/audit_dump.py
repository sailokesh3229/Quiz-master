"""One-off audit driver: dump topic lists for every chapter in a subject/class,
using the same detection path build_chunks() would use, without paying the
find_tables() cost. Used to audit Rule 1/2/3 compliance for Maths/Science."""
import sys
from pathlib import Path

from schema import build_chunks

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    class_, subject_dir, subject_label = sys.argv[1], sys.argv[2], sys.argv[3]
    base = ROOT / f"class {class_}" / subject_dir
    pdfs = sorted(base.glob("chapter *.pdf"), key=lambda p: int(p.stem.split()[1]))
    for pdf_path in pdfs:
        chunks = build_chunks(pdf_path, class_, subject_label, skip_tables=True)
        chap = chunks[0]["chapter"] if chunks else "?"
        chapnum = chunks[0]["chapter_number"] if chunks else "?"
        print(f"=== {pdf_path.stem} (chapter={chapnum} title={chap!r}) ===")
        for c in chunks:
            print(f"  [{c['topic_number']}]#{c['chunk_index']} {c['topic']!r} ({len(c['text'])} chars)")


if __name__ == "__main__":
    main()
