"""Task 5c (Step C2): detect in-text exercise questions ("Figure it Out"
boxes, numbered Q./Ans. blocks) and a chapter's own back-of-chapter answer
key, exclude them from the grounding chunk text, and store them separately
as a class/subject/chapter/topic-tagged reference corpus — each question
paired with its official printed answer.

Sample chapter for this task: class 6/maths/chapter 1.pdf (chosen over the
class 9-10 science/maths chapters used in tasks 2-5b — none of those
contain any printed in-text answers, only unanswered end-of-chapter
exercises; this book's "Figure it Out" / "Ans." format is exactly the
"boxed questions with provided answers" case PLAN.md's task 5c calls for).

Chapter structure found here: front matter has "Figure it Out" boxes with
numbered questions but NO adjacent answers; a dedicated back-of-chapter
"CHAPTER 1 — SOLUTIONS" appendix restates every question (Q1./Q.1.) with
its answer (Ans.) immediately after, tagged by "Section N.M" markers. The
reference corpus is built from that appendix (clean, reliable Q+A pairs);
the front-matter boxes are separately stripped from grounding text.

Usage: .venv/Scripts/python scripts/extract_exercises.py <path-to-pdf> <class> <subject>
"""

import json
import re
import sys
from pathlib import Path

from chunk_topics import chunk_all
from detect_headings import (
    HEADING_RE,
    MIN_HEADING_SIZE,
    _HEADING_SIZE_TOL,
    _MAX_HEADING_CHARS,
    _clean_continuation_fragment,
    _clean_heading_name,
    _detect_topics_allcaps_fallback,
    _is_corrupted_body_line,
    _looks_like_garbled_fragment,
    _looks_like_garbled_text,
    _is_real_heading,
    _looks_like_title_continuation,
    _numbered_heading_calibration,
    all_pages_lines,
)

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"

APPENDIX_START_RE = re.compile(r"^CHAPTER\s+\d+\s*[—\-–]\s*SOLUTIONS?$", re.IGNORECASE)

# Class 11 Physics audit (2026-09-03): this book numbers a chapter's
# end-of-chapter EXERCISES questions as a continuation of its own section
# numbering ("2.1 Introduction" ... "2.5 Relative velocity", then straight
# on into "2.6", "2.7", ... for the exercise questions) rather than
# restarting at "1." the way the Maths books do. HEADING_RE's shape and
# even the chapter-number+monotonic-sequence guard in
# detect_headings._is_real_heading can't tell a real section apart from
# an exercise question here — both carry the chapter's own number, both
# continue the sequence forward, and exercise questions routinely start
# with an uppercase word too ("2.5 A car moving along..."). The three
# section headings that always close out a chapter's real content in this
# book — "SUMMARY", "POINTS TO PONDER", "EXERCISES" — are a reliable,
# book-independent stop marker instead: whichever of them appears first
# reliably comes right after the last real section and before any
# exercise-numbered content, so cutting there is a safe, added-on-top
# truncation that never removes real content, only false positives past it.
#
# Class 11 Chemistry audit (2026-09-03): case-insensitive — this book
# renders the same markers as "Summary"/"exerciseS" (the latter another
# instance of this book's small-caps rendering glitch, corrupting even
# this marker's casing) rather than Physics's plain ALL-CAPS. The words
# themselves are distinctive enough on a standalone line that loosening
# the case requirement doesn't risk matching real heading/body content.
_CHAPTER_END_MARKER_RE = re.compile(r"^(SUMMARY|POINTS TO PONDER|EXERCISES?|ADDITIONAL EXERCISES)$", re.IGNORECASE)
PAGE_MARKER_RE = re.compile(r"^Page\s+No\.?\s*\d+\.?$", re.IGNORECASE)
SECTION_MARKER_RE = re.compile(r"^Section\s+(\d+\.\d+)$", re.IGNORECASE)
QUESTION_RE = re.compile(r"^Q\.?\s*(\d+)\.\s*(.*)$")
ANSWER_START_RE = re.compile(r"^Ans\.\s*(.*)$")
FIGURE_IT_OUT_RE = re.compile(r"^figure it out\s*.*$", re.IGNORECASE)
BOX_QUESTION_START_RE = re.compile(r"^\d+\.[\s\t]+")

# How close (in pt) a line's size has to be to a "Figure it Out" box's own
# question-line size to still count as part of that box (continuation or
# next numbered question), vs. having exited back to normal body text or a
# heading — task 5c finding: this book's box lines run ~12pt, body ~13pt,
# and section/summary headings 17-20pt, so a small tolerance cleanly tells
# "still in the box" apart from "back to real content".
BOX_SIZE_TOL = 1.5


def find_appendix_start_page(pdf_path: Path) -> int | None:
    """Earliest page clean_topics() should stop scanning at, whichever
    signal is present: this book's back-of-chapter "CHAPTER N — SOLUTIONS"
    appendix, or a physics-style book's "SUMMARY"/"POINTS TO
    PONDER"/"EXERCISES" chapter-end marker (see _CHAPTER_END_MARKER_RE).
    Neither signal fires in most books, and a book only ever has one of
    the two conventions, so returning whichever is found (or None if
    neither is) is safe — this never truncates a book that has no such
    marker at all.

    Class 11 Maths audit (2026-09-03): making _CHAPTER_END_MARKER_RE
    case-insensitive (for Chemistry's "Summary"/"exerciseS") surfaced a
    real conflict — this book has its own mid-chapter "Summary" recap box
    (a formula callout, not a chapter ending) appearing well before the
    real end-of-chapter Summary, and neither "trust the first occurrence"
    nor "trust the last" is safe on its own: the first picks the recap
    box here, the last re-admits a trailing worked example that shares a
    page with Chemistry's real "Summary" there. What actually
    distinguishes them: a genuine end-of-chapter block is always a
    CLUSTER — "Summary" immediately followed (within a couple of pages)
    by "Points to Ponder" and/or "Exercises" — while this book's isolated
    recap box has no such neighbor (it doesn't use a standalone
    "EXERCISES" heading at all; Maths labels its exercises "EXERCISE
    12.1" per section instead). Requiring a second marker nearby before
    trusting the first is what makes both books resolve correctly.

    That clustering rule alone reopened a second conflict, though: Class
    11 Physics prints its OWN chapter-opening "mini table of contents" —
    a sidebar restating "Summary" and "Exercises" as plain menu-item
    lines right next to each other on page 0 (Title Case, so invisible to
    the old case-sensitive match, but caught once case-insensitivity was
    added for Chemistry) — which clusters with itself trivially (same
    page) and wrongly reports chapter_end_page=0, truncating the ENTIRE
    chapter to nothing. A genuine end-of-chapter block can never
    legitimately be the chapter's own opening page or two; excluding
    those up front removes this false cluster without needing to touch
    the clustering rule that Maths depends on.
    """
    _CLUSTER_PAGE_WINDOW = 3
    _MIN_CHAPTER_END_PAGE = 2
    appendix_page = None
    end_marker_pages: list[int] = []
    for page_num, page_lines in enumerate(all_pages_lines(str(pdf_path))):
        for line in page_lines:
            text = line["text"].strip()
            if appendix_page is None and APPENDIX_START_RE.match(text):
                appendix_page = page_num
            if page_num >= _MIN_CHAPTER_END_PAGE and _CHAPTER_END_MARKER_RE.match(text):
                end_marker_pages.append(page_num)

    chapter_end_page = None
    for i, page in enumerate(end_marker_pages):
        others = end_marker_pages[:i] + end_marker_pages[i + 1 :]
        if any(abs(page - other) <= _CLUSTER_PAGE_WINDOW for other in others):
            chapter_end_page = page
            break

    candidates = [p for p in (appendix_page, chapter_end_page) if p is not None]
    return min(candidates) if candidates else None


def clean_topics(
    pdf_path: Path, appendix_start_page: int | None, exclude_titles: set[str] | None = None
) -> tuple[list[dict], int]:
    """Same accumulation shape as detect_headings.detect_topics(), but
    stops before the back-of-chapter appendix and strips "Figure it Out"
    boxed question lines from the grounding text as they're encountered."""
    topics = []
    intro_lines = []
    current = None
    removed_chars = 0

    all_lines = all_pages_lines(str(pdf_path))
    last_page = appendix_start_page if appendix_start_page is not None else len(all_lines)
    scanned_lines = all_lines[0:last_page]

    # Rule 3 follow-up (2026-09-02, Science/Maths audit): real "N.M
    # Heading" topic headings are reliably the LARGEST text matching
    # HEADING_RE's shape in a chapter — decimal numbers in body prose and
    # tables (temperature readings, worked-example measurements) also
    # match the shape but render smaller. A single global MIN_HEADING_SIZE
    # floor can't tell them apart (real heading sizes vary 12-17pt across
    # book editions), so calibrate per chapter instead of trusting the
    # floor alone. Also gates out a same-sized false positive whose
    # leading number isn't the chapter's own (class 11 Maths audit,
    # 2026-09-03) — see detect_headings._numbered_heading_calibration for
    # the full reasoning; shared with the numbered-heading path there so
    # the two don't drift out of sync again.
    heading_size, dominant_number = _numbered_heading_calibration(pdf_path, lines=scanned_lines)
    last_minor = None

    for page_lines in scanned_lines:
        heading_block = None
        in_box = False
        box_size = None

        for line in page_lines:
            text, size, block_num = line["text"], line["size"], line["block"]
            if _is_corrupted_body_line(text):
                continue

            if FIGURE_IT_OUT_RE.match(text):
                in_box = True
                box_size = None
                removed_chars += len(text)
                continue

            if in_box:
                if box_size is None or abs(size - box_size) <= BOX_SIZE_TOL or BOX_QUESTION_START_RE.match(text):
                    box_size = size if box_size is None else box_size
                    removed_chars += len(text)
                    continue
                in_box = False  # size jumped back to body/heading -> box ended

            m = HEADING_RE.match(text)
            if m and _is_real_heading(m, size, heading_size, dominant_number, last_minor):
                if current:
                    topics.append(current)
                current = {"number": f"{m.group(1)}.{m.group(2)}", "name": _clean_heading_name(m.group(3)), "text": []}
                heading_block = block_num
                last_minor = float(m.group(2))
                continue

            # Class 11 Chemistry audit (2026-09-03): this book's headings
            # sit only ~0.1pt from its own body text size (10.59 vs
            # ~10.5), so the very next paragraph after a heading clears
            # this branch's size check just as easily as a genuine wrapped
            # second heading line does — and since nothing here ever adds
            # to current["text"] while this branch keeps firing,
            # `not current["text"]` stays true and the WHOLE first
            # paragraph gets absorbed into the heading's name one line at
            # a time. The headline-capitalization shape check is the real
            # fix (a wrapped title reads like a headline, prose doesn't);
            # the length cap stays on as a hard backstop.
            if (
                current
                and not current["text"]
                and len(current["name"]) + len(text) < _MAX_HEADING_CHARS
                and abs(size - heading_size) <= _HEADING_SIZE_TOL
                and not m
                and block_num == heading_block
                and _looks_like_title_continuation(text.strip(), prev_name=current["name"])
                and not _looks_like_garbled_fragment(text.strip())
            ):
                current["name"] += " " + _clean_continuation_fragment(text)
                continue

            # A subscript rendered as its own tiny-size span (e.g. the r
            # in ∆rH) fails the size-tolerance check above and would
            # otherwise fall through to current["text"].append, which
            # permanently ends the merge window for every real
            # continuation line that follows. See detect_topics()'s
            # identical fix in detect_headings.py for the full story.
            if current and not current["text"] and block_num == heading_block and len(text.strip()) <= 3:
                continue

            if _looks_like_garbled_text(text):
                continue

            if current:
                current["text"].append(text)
            else:
                intro_lines.append(text)

    if current:
        topics.append(current)
    for t in topics:
        t["text"] = "\n".join(t["text"])

    # Rule 3 follow-up (2026-09-02, Science/Maths audit): the page-0/1
    # title-page furniture (a repeated chapter number, a glued-together
    # running-header fragment) can stand alone before the first real
    # heading with no actual introductory prose at all — found in
    # production down to a single stray "7" — which isn't the chapter's
    # real introduction and shouldn't be stored as though it were. A
    # genuine short intro (e.g. a one-line epigraph/quote, a real design
    # element in this corpus) still clears this comfortably; same
    # threshold as detect_headings.py's revised-curriculum SS detector
    # uses for the identical purpose.
    _MIN_INTRO_CHARS = 80
    intro_text = "\n".join(intro_lines)
    segments = []
    if len(intro_text) >= _MIN_INTRO_CHARS:
        segments.append({"number": None, "name": "Introduction", "text": intro_text})
    segments.extend(topics)

    if not topics:
        # task 11 finding: unnumbered-section books (Social Science) need
        # the ALL-CAPS fallback — see detect_headings.py's comment there.
        fallback = _detect_topics_allcaps_fallback(pdf_path, exclude_titles=exclude_titles)
        if any(s["number"] is not None for s in fallback):
            return fallback, removed_chars

    return segments, removed_chars


def extract_reference_corpus(pdf_path: Path, appendix_start_page: int) -> list[dict]:
    """Parse the back-of-chapter solutions appendix into Q&A pairs, tagged
    by whichever "Section N.M" marker precedes them."""
    entries = []
    current_section = None
    current_q = None
    current_a = None

    def flush():
        if current_q and current_a is not None:
            answer = " ".join(current_a).strip()
            # task 11 finding: construction/drawing questions ("Draw a
            # rectangle...", "Trace the figure...") have no text answer in
            # the book at all — the official answer IS a diagram. An empty
            # answer field doesn't serve PLAN.md's "official answer text
            # captured" goal, so skip rather than store a useless stub.
            if not answer:
                return
            entries.append(
                {
                    "topic_number": current_section,
                    "question_number": current_q["number"],
                    "question": " ".join(current_q["text"]).strip(),
                    "answer": answer,
                }
            )

    all_lines = all_pages_lines(str(pdf_path))
    for page_lines in all_lines[appendix_start_page : len(all_lines)]:
        for line in page_lines:
            text = line["text"]
            if FIGURE_IT_OUT_RE.match(text) or APPENDIX_START_RE.match(text) or PAGE_MARKER_RE.match(text):
                continue

            sm = SECTION_MARKER_RE.match(text)
            if sm:
                flush()
                current_q, current_a = None, None
                current_section = sm.group(1)
                continue

            qm = QUESTION_RE.match(text)
            if qm:
                flush()
                current_q = {"number": qm.group(1), "text": [qm.group(2)] if qm.group(2) else []}
                current_a = None
                continue

            am = ANSWER_START_RE.match(text)
            if am:
                current_a = [am.group(1)] if am.group(1) else []
                continue

            if current_a is not None:
                current_a.append(text)
            elif current_q is not None:
                current_q["text"].append(text)

    flush()
    return entries


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")

    if len(sys.argv) != 4:
        print("Usage: extract_exercises.py <path-to-pdf> <class> <subject>")
        sys.exit(1)

    pdf_path = Path(sys.argv[1])
    class_, subject = sys.argv[2], sys.argv[3]
    chapter = pdf_path.stem

    appendix_start = find_appendix_start_page(pdf_path)
    print(f"Appendix (\"CHAPTER N — SOLUTIONS\") starts on page: {appendix_start}\n")

    topics, removed_chars = clean_topics(pdf_path, appendix_start)
    chunks = chunk_all(topics)
    print(f"{removed_chars} chars of in-text exercise-box content stripped from grounding text across {len(topics)} topics\n")
    for t in topics:
        print(f"  [{t['number'] or '-'}] {t['name']!r}  ({len(t['text'])} chars grounding text)")

    corpus = extract_reference_corpus(pdf_path, appendix_start) if appendix_start is not None else []
    for entry in corpus:
        entry["class"] = class_
        entry["subject"] = subject
        entry["chapter"] = chapter

    print(f"\n{len(corpus)} question/answer pairs extracted to the reference corpus:\n")
    for e in corpus[:6]:
        print(f"  [{e['topic_number']}] Q{e['question_number']}: {e['question'][:70]!r}")
        print(f"        A: {e['answer'][:70]!r}")
    if len(corpus) > 6:
        print(f"  ... and {len(corpus) - 6} more")

    unpaired = [e for e in corpus if not e["answer"]]
    print(f"\n{len(unpaired)} entries with an empty answer (should be 0 for a clean pairing)")

    OUTPUT_DIR.mkdir(exist_ok=True)
    (OUTPUT_DIR / f"{chapter}_reference_corpus.json").write_text(
        json.dumps(corpus, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (OUTPUT_DIR / f"{chapter}_clean_chunks.json").write_text(
        json.dumps(chunks, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nWritten: {chapter}_reference_corpus.json, {chapter}_clean_chunks.json")


if __name__ == "__main__":
    main()
