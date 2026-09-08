"""Task 5: split long topics into sub-topic-sized chunks (PLAN.md Step C).

Short topics stay as a single chunk. Long topics (multi-page) split
further: by their own sub-subheadings ("1.2.1 Combination Reaction") when
present, otherwise by paragraph groups sized to a target chunk length.
Every resulting chunk keeps the SAME topic number/name plus a chunk_index,
so retrieval treats all of a topic's chunks as one topic (PLAN.md section 4).

Usage: .venv/Scripts/python scripts/chunk_topics.py <path-to-pdf>
"""

import json
import re
import sys
from pathlib import Path

from detect_headings import SUBHEADING_MARKER, detect_topics

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"

# PLAN.md Step C: "long topics (multiple pages)" get split further. A
# typical NCERT page runs ~2000-2500 chars of body text (task 3/4 sample
# chapter), so ~3000 chars is roughly "more than one page" — the "long"
# threshold. Short topics ("a paragraph or two") stay untouched.
LONG_TOPIC_CHARS = 3000

# Target size per split-out chunk when falling back to paragraph grouping
# (no sub-subheadings to split on).
TARGET_CHUNK_CHARS = 2000

SUB_SUBHEADING_RE = re.compile(r"^\d+\.\d+\.\d+\s+.{2,100}$")


def split_by_subheadings(lines: list[str]) -> list[list[str]] | None:
    """Split a topic's lines at its own sub-subheadings, if any exist."""
    boundaries = [i for i, line in enumerate(lines) if SUB_SUBHEADING_RE.match(line)]
    if not boundaries:
        return None

    pieces = []
    if boundaries[0] > 0:
        pieces.append(lines[: boundaries[0]])
    for start, end in zip(boundaries, boundaries[1:] + [len(lines)]):
        pieces.append(lines[start:end])
    return pieces


def split_by_marked_subheadings(lines: list[str]) -> list[list[str]] | None:
    """Split at SUBHEADING_MARKER boundaries (detect_headings.py's
    revised-curriculum Social Science detector) — this book's subheadings
    have no consistent numbering scheme to regex-match the way
    SUB_SUBHEADING_RE does for the numbered-heading books (some are
    lettered "a.", some numbered "A.", some bare titles with no prefix at
    all), so the detector marks the boundary explicitly instead of leaving
    it to be inferred from text shape here. Same topic number/name,
    same chunk-chaining contract as split_by_subheadings — this is just a
    different way of finding where to cut, per SPEC.md's mandatory
    topic/subtopic rule (a subheading chunks separately but stays chained
    under its parent topic, never becomes a topic of its own)."""
    boundaries = [i for i, line in enumerate(lines) if line.startswith(SUBHEADING_MARKER)]
    if not boundaries:
        return None

    pieces = []
    if boundaries[0] > 0:
        pieces.append(lines[: boundaries[0]])
    for start, end in zip(boundaries, boundaries[1:] + [len(lines)]):
        pieces.append(lines[start:end])
    return pieces


def split_by_paragraphs(lines: list[str]) -> list[list[str]]:
    """Fallback: group this topic's lines into chunks of roughly
    TARGET_CHUNK_CHARS each, breaking only at line boundaries.

    True paragraph breaks (blank lines) don't survive this pipeline's PDF
    text extraction — reconstruct_lines() (task 3) only ever emits
    non-empty lines, since NCERT's duplicate-render-for-bold artifact has
    to be filtered per-line anyway. Grouping by blank lines here would
    therefore always find zero boundaries and never split (task 5
    finding: this is exactly what happened on Chapter 5's "RESPIRATION"
    topic before this fix — 9786 chars stayed as one chunk). Grouping at
    line granularity instead still guarantees no line/sentence fragment is
    ever cut in half, which is the property PLAN.md's Step C actually cares
    about; it just isn't full-paragraph-precise.
    """
    chunks: list[list[str]] = []
    current: list[str] = []
    current_len = 0
    for line in lines:
        if current and current_len + len(line) > TARGET_CHUNK_CHARS:
            chunks.append(current)
            current = []
            current_len = 0
        current.append(line)
        current_len += len(line)
    if current:
        chunks.append(current)
    return chunks


def chunk_topic(topic: dict) -> list[dict]:
    # A SUBHEADING_MARKER is purely an internal boundary signal for the
    # split logic below — the readable subheading name right after it is
    # real content and stays, but the marker's own control character must
    # never reach stored chunk text, split or not (a topic short enough to
    # skip splitting entirely still has these embedded whenever the
    # revised-curriculum Social Science detector found any subheadings).
    text = topic["text"].replace(SUBHEADING_MARKER, "")
    if len(text) <= LONG_TOPIC_CHARS or topic["number"] is None:
        return [{"number": topic["number"], "name": topic["name"], "chunk_index": 0, "split_method": "none", "text": text}]

    lines = text.split("\n")
    pieces = split_by_marked_subheadings(topic["text"].split("\n"))
    method = "marked-subheading"
    if pieces is not None:
        pieces = [[line.replace(SUBHEADING_MARKER, "") for line in piece] for piece in pieces]
    if pieces is None:
        pieces = split_by_subheadings(lines)
        method = "sub-subheading"
    if pieces is None:
        pieces = split_by_paragraphs(lines)
        method = "paragraph-group"

    return [
        {
            "number": topic["number"],
            "name": topic["name"],
            "chunk_index": i,
            "split_method": method,
            "text": "\n".join(piece_lines).strip(),
        }
        for i, piece_lines in enumerate(pieces)
    ]


def chunk_all(topics: list[dict]) -> list[dict]:
    chunks = []
    for topic in topics:
        chunks.extend(chunk_topic(topic))
    return chunks


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")

    if len(sys.argv) != 2:
        print("Usage: chunk_topics.py <path-to-pdf>")
        sys.exit(1)

    pdf_path = Path(sys.argv[1])
    topics = detect_topics(pdf_path)
    chunks = chunk_all(topics)

    print(f"{len(topics)} topics -> {len(chunks)} chunks:\n")
    for c in chunks:
        label = c["number"] or "-"
        preview = c["text"].replace("\n", " ")[:70]
        print(f"  [{label}]#{c['chunk_index']} ({c['split_method']:<16}) {c['name']!r}  ({len(c['text'])} chars)  {preview!r}...")

    OUTPUT_DIR.mkdir(exist_ok=True)
    out_path = OUTPUT_DIR / f"{pdf_path.stem}_chunks.json"
    out_path.write_text(json.dumps(chunks, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nFull chunk dump written to {out_path}")


if __name__ == "__main__":
    main()
