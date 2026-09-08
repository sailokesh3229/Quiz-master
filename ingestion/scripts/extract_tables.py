"""Task 5b (Step C2): extract tables in structured (markdown) form, filter
out PyMuPDF's false-positive "tables", and link real ones to the chunk
they belong to; tag that chunk has_table: true.

Real-PDF finding: page.find_tables() frequently misreads NCERT's
multi-column "Activity" box layout as a table grid — on the sample
chapter, 7 of 11 raw detections were false positives spanning most of the
page with full-paragraph "cells". Genuine tables here are compact (a small
fraction of the page) with short cell values, which cleanly separates the
two (see is_real_table).

Usage: .venv/Scripts/python scripts/extract_tables.py <path-to-pdf>
"""

import json
import re
import sys
from pathlib import Path

import pymupdf

from chunk_topics import chunk_all
from detect_headings import detect_topics, topic_page_ranges

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"

MAX_AREA_RATIO = 0.15  # real tables sampled at 0.05-0.06; false positives at 0.48-0.62
MAX_CELL_LEN = 150  # real cells sampled at <=34 chars; false-positive "cells" ran 500-1700+


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "")


def is_real_table(table, page_area: float) -> bool:
    bbox = table.bbox
    area_ratio = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) / page_area
    if area_ratio > MAX_AREA_RATIO:
        return False
    cells = [c for row in table.extract() for c in row if c]
    if not cells or any(len(c) > MAX_CELL_LEN for c in cells):
        return False
    return True


def rows_to_markdown(rows: list) -> str:
    rows = [[_normalize_ws(c) for c in row] for row in rows]
    if not rows:
        return ""
    header, *body = rows
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * len(header)) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)


def extract_tables(pdf_path: Path) -> list[dict]:
    doc = pymupdf.open(pdf_path)
    found = []
    for page_num, page in enumerate(doc):
        page_area = page.rect.width * page.rect.height
        for t in page.find_tables().tables:
            if not is_real_table(t, page_area):
                continue
            rows = t.extract()
            found.append(
                {
                    "page": page_num,
                    "bbox": [round(v, 1) for v in t.bbox],
                    "rows": rows,
                    "markdown": rows_to_markdown(rows),
                }
            )
    return found


def link_tables_to_chunks(tables: list[dict], chunks: list[dict], page_ranges: list[dict]) -> list[dict]:
    """Link each table to its topic by PAGE RANGE, then narrow to a
    specific chunk within that topic by substring match where possible.

    A table's own region gets read in normal row order (reconstruct_lines
    doesn't understand grid structure), which jumbles multi-column header
    text together — e.g. "Element" + "Number of atoms..." can come out as
    "ElementNumber of atoms in..." with no separator, or split apart
    entirely (task 5b finding). Text-substring matching alone (extract_
    images.py's approach for figure captions) is therefore unreliable
    here. Page range is independent of that problem and always correct;
    substring match against a single cell is only used as a secondary
    refinement to pick the right chunk when a topic was split further.
    """
    normalized_chunks = [(c, _normalize_ws(c["text"])) for c in chunks]
    linked = []
    for tbl in tables:
        topic_range = next((r for r in page_ranges if r["first_page"] <= tbl["page"] <= r["last_page"]), None)
        topic_number = topic_range["number"] if topic_range else None
        topic_chunks = [(c, text) for c, text in normalized_chunks if c["number"] == topic_number]

        chunk_index = topic_chunks[0][0]["chunk_index"] if topic_chunks else None
        if len(topic_chunks) > 1:
            for cell in (c for row in tbl["rows"] for c in row if c):
                needle = _normalize_ws(cell)
                match = next((c for c, text in topic_chunks if needle and needle in text), None)
                if match:
                    chunk_index = match["chunk_index"]
                    break

        linked.append(
            {
                **tbl,
                "linked": topic_number is not None,
                "topic_number": topic_number,
                "chunk_index": chunk_index,
            }
        )
    return linked


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")

    if len(sys.argv) != 2:
        print("Usage: extract_tables.py <path-to-pdf>")
        sys.exit(1)

    pdf_path = Path(sys.argv[1])

    topics = detect_topics(pdf_path)
    chunks = chunk_all(topics)
    page_ranges = topic_page_ranges(pdf_path)

    tables = extract_tables(pdf_path)
    tables = link_tables_to_chunks(tables, chunks, page_ranges)

    has_table_keys = {(t["topic_number"], t["chunk_index"]) for t in tables if t["linked"]}
    for c in chunks:
        c["has_table"] = (c["number"], c["chunk_index"]) in has_table_keys

    print(f"{len(tables)} real table(s) found (false positives filtered out):\n")
    for t in tables:
        label = f"{t['topic_number']}#{t['chunk_index']}" if t["linked"] else "UNLINKED"
        print(f"  p{t['page']:>2}  chunk [{label}]  {len(t['rows'])} rows")
        print(t["markdown"])
        print()

    tagged_labels = [f"{c['number']}#{c['chunk_index']}" for c in chunks if c["has_table"]]
    print(f"{len(tagged_labels)} chunk(s) tagged has_table=True: {tagged_labels}")

    OUTPUT_DIR.mkdir(exist_ok=True)
    (OUTPUT_DIR / f"{pdf_path.stem}_tables.json").write_text(
        json.dumps(tables, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nWritten: {pdf_path.stem}_tables.json")


if __name__ == "__main__":
    main()
