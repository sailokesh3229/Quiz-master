"""Task 5a (Step C2): extract figure images + captions, link each one to the
chunk containing its caption text, and flag figure-dependent sentences.

Real-PDF finding: NCERT's technical diagrams in this sample chapter are
vector-drawn (thousands of path primitives), not embedded raster images,
and are mixed with page-wide decorative vector texture. PyMuPDF's raster
image extraction (page.get_image_info) only finds the same 1-2 background/
watermark images on *every* page regardless of content — it never finds
the actual figures. Trying to isolate diagram-only vector paths from
decorative background clutter is an open-ended problem; instead this crops
and rasterizes the page region directly above each detected "Figure N.M"
caption, which captures whatever visual content is actually there (raster
or vector) as one flattened PNG. That's robust to both content styles and
matches PLAN.md section 6's actual v1 goal: store the image for later use,
not analyze it now.

Usage: .venv/Scripts/python scripts/extract_images.py <path-to-pdf>
"""

import json
import re
import sys
from pathlib import Path

import pymupdf

from chunk_topics import chunk_all
from detect_headings import all_pages_lines, detect_topics

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"
IMAGES_DIR = OUTPUT_DIR / "images"

CAPTION_RE = re.compile(r"^Figure\s+(\d+\.\d+)\b\s*(.*)$")

# How far above a caption to crop looking for the figure, capped in case no
# text line (which would otherwise mark the figure's top edge) precedes it
# closely — e.g. a figure at the very top of a page.
MAX_CROP_HEIGHT = 400.0

# Phrasing that makes a sentence's meaning depend on seeing a figure
# (PLAN.md section 6 / Step C2's own examples, plus close variants).
FIGURE_DEPENDENT_PATTERNS = [
    r"as shown (in|below|above)",
    r"shown in (the )?figure",
    r"in the figure (below|above)",
    r"the diagram illustrates",
    r"figure\s+\d+\.\d+\s+shows",
    r"in the (above|below) (figure|diagram)",
    r"the (figure|diagram) (below|above) shows",
]
FIGURE_DEPENDENT_RE = re.compile("|".join(FIGURE_DEPENDENT_PATTERNS), re.IGNORECASE)

# Don't split right after "Fig." — it's an abbreviation, not a sentence
# end, and it's exactly the token that precedes a figure reference (e.g.
# "as shown in Fig. 1.1."), so splitting there truncated figure-dependent
# spans before the figure number (task 5a finding).
SENTENCE_SPLIT_RE = re.compile(r"(?<!Fig\.)(?<=[.!?])\s+", re.IGNORECASE)


def extract_figures(pdf_path: Path) -> list[dict]:
    """Find every "Figure N.M ..." caption, crop+rasterize the region above
    it, and save that crop as a PNG. Returns one record per figure."""
    doc = pymupdf.open(pdf_path)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    figures = []
    all_lines = all_pages_lines(str(pdf_path))  # avoids re-parsing pages already parsed by detect_topics et al.

    for page_num, page in enumerate(doc):
        page_lines = list(all_lines[page_num])
        seen_lines: list[dict] = []  # every line processed so far on this page
        i = 0
        while i < len(page_lines):
            line = page_lines[i]
            m = CAPTION_RE.match(line["text"])
            if m:
                # a caption often wraps across several short lines (narrow
                # italic caption column) — they share one PyMuPDF block, so
                # gather every following line from that same block as part
                # of the caption, same trick as the heading line-wrap fix
                # in task 3/5.
                caption_parts = [line["text"]]
                cap_bbox = list(line["bbox"])
                j = i + 1
                while j < len(page_lines) and page_lines[j]["block"] == line["block"]:
                    caption_parts.append(page_lines[j]["text"])
                    cap_bbox[2] = max(cap_bbox[2], page_lines[j]["bbox"][2])
                    cap_bbox[3] = max(cap_bbox[3], page_lines[j]["bbox"][3])
                    j += 1
                caption_text = " ".join(caption_parts)

                # Crop top = the nearest text line above the caption IN THE
                # SAME COLUMN (x-overlap), not just "whatever line came
                # first in block-iteration order" — multi-column pages
                # (task 5a finding) don't emit blocks in strict top-to-
                # bottom order, so a naive "previous line" can sit below
                # the caption and produce a negative-height crop.
                #
                # Column-matching uses the caption's FIRST line only
                # (narrow — just "Figure N.M"), not the x-range extended by
                # its wrapped continuation lines: a caption can wrap wide
                # enough to overlap an adjacent text column's x-range,
                # which let that column's body text get matched as "above,
                # same column" and produced a near-zero-height crop (task
                # 5a finding, chapter 5 Figure 5.11).
                col_x0, col_x1 = line["bbox"][0], line["bbox"][2]
                x0, y0, x1, _ = cap_bbox
                above_same_column = [
                    l
                    for l in seen_lines
                    if l["bbox"][3] <= y0 and l["bbox"][2] > col_x0 - 30 and l["bbox"][0] < col_x1 + 30
                ]
                top = max((l["bbox"][3] for l in above_same_column), default=0.0)
                top = max(top, y0 - MAX_CROP_HEIGHT)
                crop = pymupdf.Rect(max(x0 - 20, 0), top, min(x1 + 60, page.rect.width), y0)

                fname = f"{pdf_path.stem}_p{page_num}_fig{m.group(1)}.png"
                fpath = IMAGES_DIR / fname
                saved = crop.height > 5 and crop.width > 5
                if saved:
                    page.get_pixmap(clip=crop, dpi=150).save(fpath)
                else:
                    print(f"  WARNING: skipped saving p{page_num} Figure {m.group(1)} — degenerate crop {crop}", file=sys.stderr)
                figures.append(
                    {
                        "page": page_num,
                        "figure_number": m.group(1),
                        "caption": caption_text,
                        "image_file": str(fpath.relative_to(OUTPUT_DIR)).replace("\\", "/") if saved else None,
                        "crop_bbox": [round(v, 1) for v in crop],
                    }
                )
                seen_lines.append({"bbox": tuple(cap_bbox)})
                i = j
                continue

            seen_lines.append(line)
            i += 1
    return figures


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def link_figures_to_chunks(figures: list[dict], chunks: list[dict]) -> list[dict]:
    """Link each figure to the chunk whose text contains its caption —
    captions stay embedded in the surrounding body text (tasks 3-5 didn't
    strip them out), so a substring match is a direct, verifiable link.
    Whitespace is normalized on both sides: a chunk's text keeps the
    caption's lines newline-separated, while a wrapped caption (above) is
    reassembled space-separated."""
    linked = []
    normalized_chunks = [(c, _normalize_ws(c["text"])) for c in chunks]
    for fig in figures:
        needle = _normalize_ws(fig["caption"])
        owner = next((c for c, text in normalized_chunks if needle in text), None)
        linked.append(
            {
                **fig,
                "linked": owner is not None,
                # topic_number is legitimately None for the intro segment
                # (before the chapter's first heading) — that's still a
                # real link, distinct from "no matching chunk found".
                "topic_number": owner["number"] if owner else None,
                "chunk_index": owner["chunk_index"] if owner else None,
            }
        )
    return linked


def flag_figure_dependent_sentences(chunks: list[dict]) -> list[dict]:
    """Add figure_dependent_spans: sentences whose meaning depends on
    seeing a figure, kept in the chunk's text but flagged as unusable for
    question generation (PLAN.md section 6)."""
    flagged = []
    for c in chunks:
        sentences = SENTENCE_SPLIT_RE.split(c["text"].replace("\n", " "))
        spans = [s.strip() for s in sentences if s.strip() and FIGURE_DEPENDENT_RE.search(s)]
        flagged.append({**c, "figure_dependent_spans": spans})
    return flagged


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")

    if len(sys.argv) != 2:
        print("Usage: extract_images.py <path-to-pdf>")
        sys.exit(1)

    pdf_path = Path(sys.argv[1])

    topics = detect_topics(pdf_path)
    chunks = chunk_all(topics)
    chunks = flag_figure_dependent_sentences(chunks)

    figures = extract_figures(pdf_path)
    figures = link_figures_to_chunks(figures, chunks)

    print(f"{len(figures)} figures found:\n")
    for f in figures:
        label = f"{f['topic_number']}#{f['chunk_index']}" if f["linked"] else "UNLINKED"
        print(f"  p{f['page']:>2}  Figure {f['figure_number']:<6} -> chunk [{label}]  {f['image_file']}")
        print(f"        caption: {f['caption']!r}")

    total_flagged = sum(len(c["figure_dependent_spans"]) for c in chunks)
    print(f"\n{total_flagged} figure-dependent sentence(s) flagged across {len(chunks)} chunks:\n")
    for c in chunks:
        for s in c["figure_dependent_spans"]:
            print(f"  [{c['number']}#{c['chunk_index']}] {s!r}")

    OUTPUT_DIR.mkdir(exist_ok=True)
    (OUTPUT_DIR / f"{pdf_path.stem}_figures.json").write_text(
        json.dumps(figures, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (OUTPUT_DIR / f"{pdf_path.stem}_chunks_with_figures.json").write_text(
        json.dumps(chunks, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nWritten: {pdf_path.stem}_figures.json, {pdf_path.stem}_chunks_with_figures.json")


if __name__ == "__main__":
    main()
