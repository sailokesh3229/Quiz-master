"""Task 4: validate detect_headings.py's output against the chapter's own
index — i.e. every numbered heading that actually occurs in the chapter.

These per-chapter PDFs don't carry the textbook's separate front-matter
Contents page (they're already split out of the full book), so there is no
standalone index file to diff against. Instead this builds an independent
"ground truth" by scanning every reconstructed line in the chapter for
anything that *looks* like a numbered heading (any font size, both 2-level
"1.1 Title" and deeper "1.1.1 Title" patterns) — a much less restrictive
pass than detect_topics()'s MIN_HEADING_SIZE-gated one — and reports any
mismatch: a real heading the detector missed, or a false positive it added.

Usage: .venv/Scripts/python scripts/validate_topics.py <path-to-pdf>
"""

import re
import sys
from pathlib import Path

import pymupdf

from detect_headings import HEADING_RE, MIN_HEADING_SIZE, detect_topics, reconstruct_lines

# Same numbering pattern as ANY_NUMBERED_HEADING_RE in detect_headings.py,
# but capturing the depth too, and independent of any size threshold — this
# is the "did a heading-shaped line occur anywhere in the chapter" scan.
ANY_HEADING_RE = re.compile(r"^(\d+(?:\.\d+)+)\s+(.{2,100})$")


def ground_truth_headings(pdf_path: Path) -> list[dict]:
    doc = pymupdf.open(pdf_path)
    found = []
    for page_num, page in enumerate(doc):
        for line in reconstruct_lines(page):
            m = ANY_HEADING_RE.match(line["text"])
            if m:
                found.append(
                    {
                        "page": page_num,
                        "number": m.group(1),
                        "depth": m.group(1).count("."),
                        "name": m.group(2).strip(),
                        "size": line["size"],
                    }
                )
    return found


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")

    if len(sys.argv) != 2:
        print("Usage: validate_topics.py <path-to-pdf>")
        sys.exit(1)

    pdf_path = Path(sys.argv[1])

    ground_truth = ground_truth_headings(pdf_path)
    top_level_truth = [h for h in ground_truth if h["depth"] == 1]  # "N.M", the topic-boundary level

    detected = detect_topics(pdf_path)
    detected_topics = [t for t in detected if t["number"]]

    print("All numbered headings found anywhere in the chapter (any size, any depth):\n")
    for h in ground_truth:
        flag = "" if h["size"] >= MIN_HEADING_SIZE else "  (below heading-size threshold — expected: sub-subsection body text, not a topic)"
        print(f"  p{h['page']:>2}  [{h['number']}]  size={h['size']:<5}  {h['name']!r}{flag}")

    print(f"\nTop-level ('N.M') headings — these should be exactly the detected topics:")
    truth_numbers = [h["number"] for h in top_level_truth]
    detected_numbers = [t["number"] for t in detected_topics]

    print(f"  ground truth: {truth_numbers}")
    print(f"  detected:     {detected_numbers}")

    missing = [h for h in top_level_truth if h["number"] not in detected_numbers]
    extra = [n for n in detected_numbers if n not in truth_numbers]

    real_gaps = [h for h in missing if h["size"] >= MIN_HEADING_SIZE]
    explained_gaps = [h for h in missing if h["size"] < MIN_HEADING_SIZE]

    if not real_gaps and not extra:
        print("\n  MATCH — detector found exactly the chapter's real top-level sections, no more, no fewer.")
    if real_gaps:
        print(f"\n  MISSING (unexplained — a real heading the size gate should have caught): {[h['number'] for h in real_gaps]}")
    if extra:
        print(f"  EXTRA in detection (false positives): {extra}")
    for h in explained_gaps:
        print(
            f"  '{h['number']}' matched the loose numbering pattern but is body text at {h['size']}pt "
            f"(below the {MIN_HEADING_SIZE}pt heading threshold) — correctly excluded, not a real heading: {h['name']!r}"
        )

    print("\nDetected topic names (sanity-check for garbled/duplicated text):")
    for t in detected_topics:
        print(f"  [{t['number']}] {t['name']!r}")


if __name__ == "__main__":
    main()
