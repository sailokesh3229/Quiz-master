"""Task 2: extract raw text + formatting metadata (font, size, bold) from one
sample NCERT PDF, so heading-detection heuristics (task 3) have real data to
be built against.

Usage: .venv/Scripts/python scripts/extract_sample.py <path-to-pdf>
"""

import json
import sys
from collections import Counter
from pathlib import Path

import pymupdf

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"


def is_bold(span: dict) -> bool:
    # PyMuPDF sets bit 4 (16) of "flags" for bold; font name often also
    # carries "Bold"/"bd" — real NCERT PDFs are inconsistent about which
    # signal is set, so we check both.
    return bool(span["flags"] & 2**4) or "bold" in span["font"].lower()


def extract_spans(pdf_path: Path) -> list[dict]:
    doc = pymupdf.open(pdf_path)
    spans = []
    for page_num, page in enumerate(doc):
        raw = page.get_text("dict")
        for block in raw["blocks"]:
            if block["type"] != 0:  # skip image blocks here; task 5a handles images
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    text = span["text"].strip()
                    if not text:
                        continue
                    spans.append(
                        {
                            "page": page_num,
                            "text": text,
                            "font": span["font"],
                            "size": round(span["size"], 1),
                            "bold": is_bold(span),
                            "color": span["color"],
                            "bbox": [round(v, 1) for v in span["bbox"]],
                        }
                    )
    return spans


def summarize(spans: list[dict]) -> None:
    style_counts = Counter((s["size"], s["bold"], s["font"]) for s in spans)
    print(f"\n{len(spans)} text spans extracted, {len(style_counts)} distinct (size, bold, font) styles\n")
    print(f"{'size':>6} {'bold':>5}  {'font':<30} {'count':>6}  sample text")
    print("-" * 100)
    for (size, bold, font), count in sorted(style_counts.items(), key=lambda kv: (-kv[0][0], -kv[1])):
        sample = next(s["text"] for s in spans if s["size"] == size and s["bold"] == bold and s["font"] == font)
        print(f"{size:>6} {str(bold):>5}  {font:<30} {count:>6}  {sample[:50]!r}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")

    if len(sys.argv) != 2:
        print("Usage: extract_sample.py <path-to-pdf>")
        sys.exit(1)

    pdf_path = Path(sys.argv[1])
    spans = extract_spans(pdf_path)
    summarize(spans)

    OUTPUT_DIR.mkdir(exist_ok=True)
    out_path = OUTPUT_DIR / f"{pdf_path.stem}_spans.json"
    out_path.write_text(json.dumps(spans, indent=2))
    print(f"\nFull span dump written to {out_path}")


if __name__ == "__main__":
    main()
