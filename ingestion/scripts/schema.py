"""Task 6: the chunk metadata schema (PLAN.md section 4) implemented in
code, consolidating what tasks 3-5c each built separately:
- detect_headings.py / extract_exercises.py: topic boundaries, with
  in-text exercise boxes and the back-of-chapter answer appendix already
  excluded from grounding text
- chunk_topics.py: long-topic splitting (chunk_index)
- extract_images.py: figures/captions per chunk, figure-dependent spans
- extract_tables.py: has_table + structured markdown per chunk
- extract_exercises.py: the separate exercise/answer reference corpus

build_chunks() returns one dict per chunk matching PLAN.md's chunk record
exactly (chunk_id, class, subject, chapter, chapter_number, topic,
topic_number, chunk_index, text, has_table, figure_dependent_spans,
images, embedding=None — filled in by task 7).

build_reference_corpus() returns the separate table for in-text exercise
questions + their official answers.

Usage: .venv/Scripts/python scripts/schema.py <path-to-pdf> <class> <subject>
"""

import functools
import hashlib
import json
import re
import sys
from pathlib import Path

import pymupdf

from chunk_topics import chunk_all
from detect_headings import (
    _collapse_repeated_runs,
    detect_topics_chem11_ch8,
    detect_topics_physics_allcaps_numbered,
    detect_topics_ss_class10_history,
    detect_topics_ss_class10_polsci,
    detect_topics_ss_revised_curriculum,
    normalize_topic_display_name,
    reconstruct_lines,
    topic_page_ranges,
)
from extract_exercises import clean_topics, extract_reference_corpus, find_appendix_start_page
from extract_images import extract_figures, flag_figure_dependent_sentences, link_figures_to_chunks
from extract_tables import extract_tables, link_tables_to_chunks

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"
_CHAPTER_TITLES_TSV = OUTPUT_DIR / "chapter_titles_final.tsv"

# SPEC.md section 6 / PLAN.md task 15: which (class, subject) pairs use the
# revised-curriculum Social Science extraction architecture (font-size-tiered
# headings, not numbered or ALL-CAPS) — verified against class 6's own 14
# chapters, class 7's own 20, class 8's own 15, then class 9's own 9
# (2026-09-02; a differently-designed title page — "Chapter"/digit/"Big
# Questions" render differently than classes 6-8's, but all safely outside
# both heading-size ranges regardless — needed no new fix beyond raising
# the empty-topic filter to a minimum content length, not just non-empty:
# found in production, a heading sitting at the bottom of a page directly
# above a table (whose own text reads out of order ahead of it, a
# pre-existing table-layout limitation) had nothing after it but a
# running-header line before the next real heading). This allowlist is
# intentional, not an auto-detected heuristic — extending it to another
# class is a deliberate, verified decision each time, per the user's
# "one textbook at a time" process. All 4 classes sharing this
# revised-curriculum Social Science design (6-9) are now verified.
_REVISED_CURRICULUM_SS_CLASSES = {"6", "7", "8", "9"}


def _uses_revised_curriculum_ss_detector(class_: str, subject: str) -> bool:
    return subject.strip().lower() == "social science" and class_.strip() in _REVISED_CURRICULUM_SS_CLASSES


# Class 10 Social Science bundles 4 sub-books (Geography, Economics,
# History, Political Science — the same 4-sub-book split already
# documented for chapter-title extraction, section 3a) under one class,
# each with its own genuinely different heading design: History already
# uses the numbered "N.M Heading" path directly (detect_topics() never
# falls through to the ALL-CAPS fallback for it); Geography/Economics use
# the (now-hardened) ALL-CAPS fallback; Political Science's real headings
# are Title Case, not ALL-CAPS at all, so it needs this THIRD, dedicated
# font-size-tiered detector (verified across all 5 of its chapters,
# 2026-09-02) — an explicit chapter-number allowlist, not auto-detected,
# same "one textbook/sub-book at a time, deliberately verified" reasoning
# as _REVISED_CURRICULUM_SS_CLASSES above.
_CLASS10_POLSCI_CHAPTERS = {"18", "19", "20", "21", "22"}


def _uses_class10_polsci_detector(class_: str, subject: str, pdf_path: Path) -> bool:
    if not (subject.strip().lower() == "social science" and class_.strip() == "10"):
        return False
    m = re.search(r"\d+", pdf_path.stem)
    return bool(m) and m.group() in _CLASS10_POLSCI_CHAPTERS


# Class 10 Social Science's History sub-book (task 15 follow-up,
# 2026-09-02, found by the user reviewing the app): was routed through
# the generic numbered-heading path (clean_topics/detect_topics), which
# treats "N.M" as chapter.section — wrong here, where the book's own
# scheme is section.subsection and only the bare "N" is a real topic.
# Explicit chapter-number allowlist, same reasoning as the Political
# Science one above.
_CLASS10_HISTORY_CHAPTERS = {"13", "14", "15", "16", "17"}


def _uses_class10_history_detector(class_: str, subject: str, pdf_path: Path) -> bool:
    if not (subject.strip().lower() == "social science" and class_.strip() == "10"):
        return False
    m = re.search(r"\d+", pdf_path.stem)
    return bool(m) and m.group() in _CLASS10_HISTORY_CHAPTERS


# Class 11/12 Physics/Chemistry/Biology audit (2026-09-03): verified
# against all 14 chapters of Class 11 Physics — real headings are
# ALL-CAPS "N.M HEADING", but the same chapter also prints a Title-Case
# "mini table of contents" restating every heading on its own opening
# page, and numbers end-of-chapter EXERCISES questions as a continuation
# of the same per-chapter decimal scheme (see detect_topics_physics_
# allcaps_numbered's docstring for the full reasoning). Chemistry turned
# out to use a genuinely different, mixed-case convention and was NOT
# added here (see the default-path headline-fragment gate in
# _is_real_heading instead) — Biology was verified separately
# (2026-09-03) against all 19 chapters of Class 11 Biology and confirmed
# to share Physics's exact design (same sidebar mini-TOC duplication,
# same ALL-CAPS real headings), so it's added here rather than needing
# its own copy of the same detector.
_ALLCAPS_NUMBERED_CLASSES = {"11", "12"}
_ALLCAPS_NUMBERED_SUBJECTS = {"physics", "biology"}


def _uses_physics_allcaps_detector(class_: str, subject: str) -> bool:
    return class_.strip() in _ALLCAPS_NUMBERED_CLASSES and subject.strip().lower() in _ALLCAPS_NUMBERED_SUBJECTS


def _uses_chem11_ch8_detector(class_: str, subject: str, pdf_path: Path) -> bool:
    return class_.strip() == "11" and subject.strip().lower() == "chemistry" and pdf_path.stem == "chapter 8"

# Lines this short, this small, and repeating on several early pages are
# almost certainly a running header carrying the chapter's own title —
# cleaner and already-cased than the stylized cover-page rendering.
#
# Class 12 Physics audit (2026-09-03): "Solution" is a real recurring
# label in this book (every worked example ends one), title-shaped
# (short, Title Case, no trailing punctuation) and frequent enough
# — this book's real title never repeats as a running header at all
# (only once, in the page-0 banner), so a structural in-body label like
# this can out-frequency it and win the tier-1 vote outright (found in
# production: chapter 5 and 6 both title'd "Solution"). No safe general
# shape rule tells "Solution" apart from a real one-word chapter title
# elsewhere in this same corpus ("Gravitation", "Thermodynamics"), so
# it's excluded by name, the same way the reprint watermark below is.
_SKIP_HEADER_TEXT = {"solution", "solutions"}

# The reprint watermark's year changes release to release ("Reprint
# 2026-27" in most of this corpus, "Reprint 2025-26" found in Class 12
# Physics) — matching it as a pattern rather than one hardcoded year
# string is what makes this exclusion keep working as new editions with
# different watermark years enter the corpus.
_REPRINT_WATERMARK_RE = re.compile(r"^reprint\s+\d{4}(-\d{2,4})?$", re.IGNORECASE)


_FIGURE_OR_TABLE_LABEL_RE = re.compile(r"^(fig(ure)?|table)\.?\s*\d", re.IGNORECASE)
# A diagram's point label ("A (3, 4)", "M (9, 6)") — single letter plus a
# parenthesized coordinate pair, repeated across the handful of pages one
# diagram spans, which can coincidentally hit the same-page-count bar a
# real running header needs (found in production: class 9 Maths chapter 1,
# a coordinate-geometry chapter where several of these repeat 3x while
# the real title never repeats as a running header at all).
_COORDINATE_LABEL_RE = re.compile(r"^[A-Za-z]\s*\([\d,.\s\-]+\)$")

# Minor words a real headline-style chapter title correctly keeps
# lowercase ("A Peek Beyond the Point", "Parallel and Intersecting
# Lines") — Python's str.istitle() requires *every* word capitalized and
# rejects these as not title-case, which was rejecting real titles
# corpus-wide (found in production: class 7 Maths regressed from correct
# titles to "Unknown" because of this).
_TITLE_MINOR_WORDS = {
    "a", "an", "the", "of", "in", "on", "and", "or", "to", "for", "at",
    "by", "with", "from", "as", "is", "are", "it", "its", "his", "her",
    "our", "your", "their", "up", "down", "into", "onto", "&",
}


def _is_headline_title_case(body: str) -> bool:
    words = body.split()
    if not words:
        return False
    for i, w in enumerate(words):
        core = w.strip(".,:;!?()'\"")
        if not core or not core[0].isalpha():
            continue
        if i > 0 and core.lower() in _TITLE_MINOR_WORDS:
            continue
        if not core[0].isupper():
            return False
    return True


def _looks_like_title_candidate(t: str) -> bool:
    """A real chapter title (this corpus's convention: short, ALL-CAPS or
    Title Case, no trailing sentence punctuation) vs. an ordinary sentence
    fragment, in-text figure/table label ("Fig. 4.1"), or short recurring
    artifact ("abc", "i.e.,") that the same low-size line scan also picks
    up. Applied both to the repeated-running-header tier (so a 3-letter
    fixture that happens to repeat exactly as often as the real title
    can't win by frequency alone) and the page-0/1 single-occurrence
    fallback tier below (where there's no repeat-count to lean on at all)."""
    body = t.strip()
    # A lone capital letter is almost always a decorative drop-cap (the
    # oversized first letter of the chapter's opening paragraph, not the
    # title) — found in production (class 10 Science chapter 7): a
    # drop-cap "B" ("Before we discuss...") rendered at 30.1pt, indistin-
    # guishable in size from the real 30pt title, won this tier outright
    # since nothing here rejected single-character text. `_big_font_title`
    # below already excludes these on the same reasoning; this tier needs
    # the identical guard.
    # Found in production (class 9 Maths ch8): a real title can run past
    # 60 chars for this corpus's longer, colon-containing naming style
    # ("Predicting What Comes Next: Exploring Sequences and
    # Progressions", 66 chars) — the old 60-char cap rejected it outright,
    # so a same-colored recurring feature-box label ("Think and Reflect")
    # elsewhere on the page won by default as the only surviving
    # candidate. Raised to 80, matching the cap `_big_font_title` below
    # already uses for the identical "is this really a title" judgment;
    # the existing 8-word cap a few lines down still guards against a
    # long run of prose being mistaken for a title.
    if len(body) <= 1 or len(body) > 80 or body[-1] in ".!?,;:":
        return False
    if not body[0].isupper():
        return False
    if _FIGURE_OR_TABLE_LABEL_RE.match(body) or _COORDINATE_LABEL_RE.match(body):
        return False
    # A bare "SECTION" label, optionally with a roman-numeral prefix
    # ("II SECTION") — a multi-part book's structural divider, not a
    # title, exactly like a bare "Chapter N" — found in production
    # winning outright via the color-banner tier (its roman numeral is
    # part of the same merged run, so the bare-number structural-token
    # check doesn't catch it) ahead of the real, uncolored descriptive
    # title elsewhere on the page ("Livelihoods, Economies and
    # Societies"). Reject it here so every tier falls through to that
    # real title instead of stopping at the divider label.
    if re.fullmatch(r"(?:[IVXLCDM]+\s+)?SECTION\.?", body, re.IGNORECASE):
        return False
    if len(body.split()) > 8:
        return False
    # A single run-together "word" (no internal whitespace) longer than
    # any real single-word title in this corpus's convention (the
    # longest confirmed genuine one is 14 characters, "Quadrilaterals")
    # is a strong sign of concatenated words that lost their spaces and
    # leading letters to a duplicate-offset rendering bug elsewhere in
    # this pipeline ("NDERSTANDINGCONOMICEVELOPMENT" for "Understanding
    # Economic Development" — each of the 3 words is missing its own
    # leading letter, consistent with the same character-dedup mechanism
    # that drops a leading character from single-word repeats elsewhere).
    if " " not in body and len(body) > 20:
        return False
    alpha = [c for c in body if c.isalpha()]
    if not alpha:
        return False
    # A title ending on a bare connector word ("Includes the", "Business
    # or", "Structure of") is essentially always a merge that dropped the
    # real final word(s), not a genuine title — found repeatedly in
    # production across otherwise-unrelated chapters/books. Real titles
    # don't grammatically end here.
    last_word = re.sub(r"[^a-zA-Z]", "", body.split()[-1]).lower()
    if last_word in _TITLE_MINOR_WORDS:
        return False
    upper_ratio = sum(1 for c in alpha if c.isupper()) / len(alpha)
    return body.isupper() or upper_ratio >= 0.6 or _is_headline_title_case(body)


def _reattach_dropcap_letters(candidates: list[dict]) -> list[dict]:
    """Reattach a raised drop-cap first letter to the word it belongs to,
    before any other merging happens.

    Found in production (class 10 Maths, e.g. "Polynomials" -> "P" +
    "OLYNOMIALS", "Introduction to Trigonometry" -> "I" + "NTRODUCTION" +
    " TO" on line 1, "T" + "RIGONOMETRY" on line 2): this book renders
    every title's first letter (of possibly more than one word) as its
    own oversized span. A drop-cap span and its remainder sit on the same
    visual line but do NOT share a top edge (the drop-cap is taller and
    starts higher) — they share a BOTTOM edge instead, which the general
    row/wrap merge in `_color_banner_runs` below doesn't check (it keys
    off top-edge alignment, tuned for same-size same-row continuations
    and stacked wrapped lines). Handled here as its own narrow pass, on
    the original fine-grained spans, before that general merge runs — the
    combined "INTRODUCTION"/"TRIGONOMETRY" pieces this produces then flow
    into the existing merge exactly like any other same-size run would.

    A drop-cap with no horizontally-adjacent partner (e.g. class 10
    Science ch7's "B", a decorative first letter of a body paragraph, not
    of any title) is left as its own unmerged single-character candidate
    — unchanged from today's behavior, since nothing here removes it."""
    dropcaps = [c for c in candidates if re.fullmatch(r"[A-Za-z]", c["text"].strip())]
    if not dropcaps:
        return candidates
    dropcap_ids = {id(c) for c in dropcaps}

    consumed_ids = set()
    merged_by_dropcap_id = {}
    for cap in dropcaps:
        cap_bbox = cap["bbox"]
        matches = [
            c for c in candidates
            if id(c) not in dropcap_ids
            and c["color"] == cap["color"]
            and id(c) not in consumed_ids
            # A real drop-cap is always the LARGER of the pair (enlarged
            # by definition) — found in production, class 6 Social
            # Science ch6: a genuinely tiny 'C' (16pt, part of an
            # unrelated decorative glyph cluster, not a title at all)
            # spatially happened to sit next to a huge 72pt garbled
            # symbol and wrongly "recombined" with it. Requiring the
            # drop-cap be >= its partner rules that out without
            # affecting any real case (28pt cap / 19.6pt remainder, etc.)
            and cap["size"] >= c["size"]
            and abs(c["bbox"][0] - cap_bbox[2]) < 10
            and c["bbox"][1] < cap_bbox[3] and c["bbox"][3] > cap_bbox[1]
        ]
        if len(matches) == 1:
            partner = matches[0]
            merged_by_dropcap_id[id(cap)] = {
                "text": cap["text"].strip() + partner["text"],
                "size": max(cap["size"], partner["size"]),
                "color": cap["color"],
                "bbox": (
                    min(cap_bbox[0], partner["bbox"][0]),
                    min(cap_bbox[1], partner["bbox"][1]),
                    max(cap_bbox[2], partner["bbox"][2]),
                    max(cap_bbox[3], partner["bbox"][3]),
                ),
            }
            consumed_ids.add(id(cap))
            consumed_ids.add(id(partner))

    return [merged_by_dropcap_id[id(c)] for c in candidates if id(c) in merged_by_dropcap_id] + [
        c for c in candidates if id(c) not in consumed_ids and id(c) not in merged_by_dropcap_id
    ]


def _color_banner_runs(page: "pymupdf.Page") -> list[dict]:
    """Groups page-0 text by color into runs, restricted to text that is
    NOT the page's dominant ("body") color and large enough to be a
    heading (>13pt) — found in production to be a strong, largely
    era-independent signal for chapter titles across this corpus: a
    professionally laid-out title (or "Chapter N" label) consistently
    uses a distinct display color from body text, regardless of whether
    the book uses a light-on-dark banner or a dark-on-light one, and
    regardless of book production era (verified on both an InDesign-era
    class 6 Science book and an old PageMaker-era class 10 Maths book —
    "REAL NUMBERS" is colored there too, just not white-on-banner).

    Grouped by color alone, not (color, size): a stylized drop-cap title
    can render alternating letters/words at two different sizes within
    the *same* color ("R" at 28pt + "EAL" at 19.6pt, both one color, for
    "REAL") — grouping by size too would split a single title into
    unmergeable pieces. Within one color's group, spans are merged when
    the vertical gap between them is small (a wrapped title's second
    line sits right below the first); this is deliberately keyed off
    color rather than "the next span in y-order across all colors",
    because an unrelated differently-colored element (e.g. the chapter
    number) can sit spatially between a wrapped title's two lines and
    would otherwise break the merge."""
    from collections import Counter

    d = page.get_text("rawdict")
    color_count: Counter = Counter()
    spans = []
    for block in d["blocks"]:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                text = _collapse_repeated_runs("".join(c["c"] for c in span["chars"]))
                if not text.strip():
                    continue
                color_count[span["color"]] += len(text)
                spans.append({"text": text, "size": span["size"], "color": span["color"], "bbox": span["bbox"]})
    if not color_count:
        return []

    # A superscript ordinal suffix ("th"/"st"/"nd"/"rd" — e.g. "13th")
    # renders as its own small, slightly-raised span immediately after
    # the numeral's own span (found in production: a History chapter
    # title with a date range, "13th to 17th Centuries", split into "13"
    # + "th" + " to 17" + "th" + " Centuries" as five separate same-color
    # spans). The vertical offset — small, but enough to round to a
    # different row bucket than its neighbor — breaks the row/adjacency
    # merge below before it ever gets a chance, scattering the title into
    # disconnected runs that never remerge ("Cultural Currents: 13" and
    # " to 17" ending up as two separate, never-reunited candidates).
    # Spans are already in natural left-to-right reading order at this
    # point (guaranteed within one PyMuPDF line) — glue the suffix onto
    # its immediate predecessor right here, before row-sorting ever runs,
    # rather than teaching the row-based merge below about superscripts
    # in general.
    merged_spans = []
    for s in spans:
        if merged_spans and s["text"].strip().lower() in ("st", "nd", "rd", "th") and s["color"] == merged_spans[-1]["color"]:
            prev = merged_spans[-1]
            prev["text"] += s["text"].strip()
            prev["bbox"] = (
                prev["bbox"][0], prev["bbox"][1],
                max(prev["bbox"][2], s["bbox"][2]), max(prev["bbox"][3], s["bbox"][3]),
            )
        else:
            merged_spans.append(dict(s))
    spans = merged_spans
    body_color = color_count.most_common(1)[0][0]

    candidates = [s for s in spans if s["color"] != body_color and s["size"] > 13]
    candidates = _reattach_dropcap_letters(candidates)

    # A decorative chapter-number digit or the word "Chapter" is a
    # structural page ornament, not title text — and one found in
    # production is large enough to visually *overlap* the title's own
    # bbox (a huge background "1" the title sits in front of, both the
    # same color), which would otherwise merge into it as "1The
    # Wonderful World of Science". Excluded from the merge pool entirely
    # (not merely treated as a chain-breaker mid-loop, which would itself
    # wrongly truncate a real multi-line title that happens to have one
    # of these sitting between its lines) and handled as its own
    # separate, unmerged pass purely for chapter_number below.
    # Class 11 Biology audit (2026-09-03): a drop-cap "C" (of "CHAPTER")
    # can end up with no reattachment partner at all — not because it's
    # a real standalone element, but because its own remainder ("HAPTER")
    # renders at this book's smaller chapter-label size, below this
    # tier's own size>13 candidate floor, so it was never even in the
    # pool `_reattach_dropcap_letters` could pair it from. Left in
    # text_pool, this bare "C" then merges into the real title on the
    # line below via ordinary vertical-stack adjacency ("C Biological
    # Classification" instead of "Biological Classification"). A lone
    # single letter is never legitimate title text on its own (the same
    # reasoning `_big_font_title` below already applies via its own
    # length-1 exclusion) — treating it as structural here, before the
    # merge pass ever runs, keeps it out of the merge instead of relying
    # on a later shape check to catch it only when it stays unmerged.
    def _is_structural_token(s: dict) -> bool:
        t = s["text"].strip()
        return bool(re.fullmatch(r"\d{1,2}", t)) or t.lower() in ("chapter",) or (len(t) == 1 and t.isalpha())

    structural = [s for s in candidates if _is_structural_token(s)]
    text_pool = [s for s in candidates if not _is_structural_token(s)]

    by_color: dict[int, list[dict]] = {}
    for s in text_pool:
        by_color.setdefault(s["color"], []).append(s)

    runs: list[dict] = [dict(s) for s in structural]
    for group in by_color.values():
        group.sort(key=lambda s: (round(s["bbox"][1]), s["bbox"][0]))
        merged = None
        # The y0 of the most recently added individual span — distinct
        # from merged["bbox"][1], which is the MIN y0 across the whole
        # accumulated region and stays pinned to the first line forever
        # once a second line has merged in. Found in production: a
        # wrapped second line broken into several sub-spans by a
        # superscript ("13" + "th" + " to 17" + "th" + " Centuries", all
        # one visual line) — after the first sub-span merges in, the
        # accumulated bbox already spans both the first AND second lines
        # vertically, so the *next* sub-span's y0 (still on line 2) reads
        # as deeply "above" that combined region's bottom edge and fails
        # both the same-row and line-wrap checks below, even though it's
        # plainly continuing the same row the previous sub-span was just
        # added from. Row/adjacency comparisons need the last actual row
        # seen, not the whole merged region's vertical extent.
        last_row_y0 = None
        last_appended_text = None
        for s in group:
            # Two spans on the same visual row — PyMuPDF can split one
            # line into multiple span records mid-word on a font/style
            # change even with no color change (found in production:
            # "Exploring Alge" + "braic" continue on one line, but
            # pymupdf gives them nearly-equal y0 with meaningfully
            # different y1, since their bbox *heights* differ slightly) —
            # merge horizontally whenever their tops nearly coincide and
            # the next one starts at or soon after the previous one ends.
            same_row = merged is not None and abs(s["bbox"][1] - last_row_y0) < 5
            horizontally_adjacent = same_row and (s["bbox"][0] - merged["bbox"][2]) < 10

            # Consecutive *wrapped* lines (stacked, not same-row) can have
            # a substantially negative gap (bbox overlap from
            # line-height/descenders, or a whole second line's box
            # starting near the first line's own top — found in
            # production: a 2-line title split mid-word, "Exploring
            # Algebraic" / "Identities", overlapping by 21pt) — allow
            # that whenever the two spans start at the same left margin
            # (a strong same-block-wrapped-line signal), since the one
            # case that needed "no overlap at all" regardless of x (a
            # huge decorative number underlaying the title) is already
            # excluded from this pool entirely via _is_structural_token.
            same_left_margin = abs(s["bbox"][0] - merged["bbox"][0]) < 2 if merged is not None else False
            gap_lower_bound = -25 if same_left_margin else -15
            vertical_stack_ok = merged is not None and gap_lower_bound <= (s["bbox"][1] - merged["bbox"][3]) < 20

            if horizontally_adjacent or vertical_stack_ok:
                # Class 12 Chemistry audit (2026-09-03): some chapters use
                # a much heavier decorative repeat effect than the usual
                # 2-3x bold-simulation duplicate elsewhere in this corpus
                # — the WHOLE title line printed 5 times as separate,
                # fully-formed spans, near enough to each other to keep
                # merging via the adjacency checks above ("Unit" x5,
                # "Haloalkanes and" x5). Each repeat is byte-identical to
                # the one just merged in, unlike a genuine two-word title
                # ("Unit 6") or a real wrapped second line, which never
                # exactly repeat the immediately preceding span — so
                # skipping a span whose text exactly matches the last one
                # actually appended (still extending the bbox, since the
                # duplicate's ink is still part of the same visual title)
                # collapses the repeat without touching genuinely distinct
                # adjacent/stacked text.
                is_exact_repeat = s["text"].strip() == last_appended_text
                if not is_exact_repeat:
                    sep = "" if merged["text"].endswith((" ", "-")) or s["text"].startswith(" ") else " "
                    merged["text"] += sep + s["text"]
                    last_appended_text = s["text"].strip()
                merged["size"] = max(merged["size"], s["size"])
                merged["bbox"] = (
                    min(merged["bbox"][0], s["bbox"][0]),
                    min(merged["bbox"][1], s["bbox"][1]),
                    max(merged["bbox"][2], s["bbox"][2]),
                    max(merged["bbox"][3], s["bbox"][3]),
                )
                last_row_y0 = s["bbox"][1]
            else:
                if merged is not None:
                    runs.append(merged)
                merged = dict(s)
                last_appended_text = s["text"].strip()
                last_row_y0 = s["bbox"][1]
        if merged is not None:
            runs.append(merged)
    return runs


def _to_headline_case(body: str) -> str:
    """ALL-CAPS source text -> proper headline case (minor words stay
    lowercase, e.g. "the"), unlike str.title() which capitalizes every
    word including those and would turn "A Peek Beyond the Point" into
    the wrong "A Peek Beyond The Point"."""
    words = body.split(" ")
    out = []
    for i, w in enumerate(words):
        lower = w.lower()
        core = lower.strip(".,:;!?()'\"")
        if i > 0 and core in _TITLE_MINOR_WORDS:
            out.append(lower)
        else:
            out.append(lower[:1].upper() + lower[1:] if lower else lower)
    return " ".join(out)


def _color_banner_title_and_number(page: "pymupdf.Page") -> dict:
    """Tier 0: the color-banner signal above, filtered to a real
    title-shaped run (longest survivor — a wrapped multi-line title
    merges into the longest run, while a lone "Chapter"/number label
    stays short) plus a same-pass bare-number run for chapter_number."""
    from collections import Counter

    runs = _color_banner_runs(page)
    title = None
    number = None

    # Class 11/12 Chemistry audit (2026-09-03): this book (like several
    # NCERT Physics/Chemistry/Biology books) groups its chapters under a
    # printed "UNIT N" banner on the SAME page as, and often in a more
    # visually prominent color than, the chapter's own real title — found
    # in production winning tier 0 outright ("Unit 2" instead of the real
    # "Structure of Atom"). A bare "UNIT" label (with or without its
    # number) is exactly the same kind of structural token as "CHAPTER"
    # or a bare page-number digit — never real title text on its own.
    runs = [r for r in runs if not re.fullmatch(r"unit\s*[ivxlcdm\d]*\.?", r["text"].strip(), re.IGNORECASE)]

    # Class 11/12 Chemistry audit (2026-09-03): some chapters in this book
    # render their title in a decorative small-caps style that's visually
    # capitalized on the page but encoded in the PDF as plain lowercase
    # text ("structure of atom") — found in production failing this
    # tier's shape check outright (which requires a real title to start
    # uppercase), leaving the "UNIT N" label above as the only surviving
    # candidate once that exclusion also applies. Restricted to exactly
    # this tier: `_color_banner_runs` has already limited these candidates
    # to large (>13pt), non-body-color, page-0 text, a much narrower and
    # safer context for "an all-lowercase run is probably a stylized
    # title" than the shared shape-checker's other callers (tier 1's
    # running-header vote, where an all-lowercase repeated fragment is
    # more plausibly ordinary body prose) would be.
    # A stray mid-word capital ("sOme", "PrinciPles" for "Some"/
    # "Principles") is the same small-caps encoding artifact, just
    # inconsistently applied rather than uniformly lowercased — found in
    # production, same book, chapter 8's title. A real title's capitals
    # only ever start a word (preceded by whitespace or nothing); a
    # capital directly preceded by a lowercase letter never happens in
    # genuine title text, so it's a reliable, narrow tell independent of
    # the simpler "the whole run is lowercase" case above.
    def _has_corrupted_midword_capitals(s: str) -> bool:
        return any(s[i].isupper() and s[i - 1].isalpha() and s[i - 1].islower() for i in range(1, len(s)))

    normalized_runs = []
    for r in runs:
        t = r["text"].strip()
        needs_normalizing = t.islower() or _has_corrupted_midword_capitals(t)
        normalized_runs.append({**r, "text": _to_headline_case(t.lower())} if needs_normalizing else r)
    runs = normalized_runs

    # Structural tokens ("CHAPTER", a bare number) are real, complete,
    # shape-valid text on their own — never candidates for the title.
    text_candidates = [
        r for r in runs
        if not re.fullmatch(r"\d{1,2}", r["text"].strip()) and r["text"].strip().lower() != "chapter"
        and _looks_like_title_candidate(r["text"].strip())
    ]
    # If one color produced more than one surviving shape-valid fragment,
    # the merge above likely failed to stitch a single title back
    # together rather than there genuinely being two unrelated titled
    # elements in the same display color (found in production: a
    # two-word drop-cap title, "REAL" / "NUMBERS" each with an
    # enlarged-and-raised first letter, split into four independently
    # shape-valid fragments — "R", "N", "EAL", "UMBERS" — that never
    # remerge under any reasonable line-gap tolerance since the raised
    # capitals and the lowered remainders don't line up as rows at all).
    # Abstain for that color rather than confidently pick the wrong
    # fragment; the existing tiers below still have a shot at this page —
    # UNLESS the group is exactly a real title plus one same-colored
    # extra element (found in production, class 9 Maths ch8: the real
    # title and a recurring "Think and Reflect" feature-box label both
    # survived as separate same-color candidates once the length cap
    # above was loosened; the real title is reliably the bigger of the
    # two, the same "prefer the largest font" reasoning already used
    # below for cross-color ties). Deliberately scoped to EXACTLY two
    # survivors: a title shattered into 3+ same-color fragments (found in
    # production, class 10 Maths: a per-word drop-cap style rendering
    # "AREAS AND V" / "OLUMES" / etc. as separate runs) has no reliable
    # "pick the biggest" answer — sizes there alternate by drop-cap vs.
    # remainder, not by which fragment is real, so abstaining and letting
    # a lower tier find the title is still the only safe move for 3+.
    color_counts = Counter(r["color"] for r in text_candidates)
    by_color: dict[int, list[dict]] = {}
    for r in text_candidates:
        by_color.setdefault(r["color"], []).append(r)
    text_candidates = []
    for color, group in by_color.items():
        if color_counts[color] == 1:
            text_candidates.extend(group)
            continue
        if len(group) != 2:
            continue
        max_size = max(r["size"] for r in group)
        biggest = [r for r in group if r["size"] == max_size]
        if len(biggest) == 1:
            text_candidates.append(biggest[0])
    if text_candidates:
        # When more than one differently-colored shape-valid candidate
        # survives (found in production: a recurring section-feature
        # label — "Big Questions" — colored distinctly from, and
        # appearing alongside, the real title on every chapter of one
        # book), prefer the largest font: a real title is reliably the
        # single biggest piece of accent-colored display text on its
        # page, while a recurring feature label is a smaller, secondary
        # element. "Longest text" alone picked the wrong one here (the
        # label read longer than the real one-word title).
        best = max(text_candidates, key=lambda r: (r["size"], len(r["text"])))
        title = best["text"].strip()
        if title.isupper():
            title = _to_headline_case(title)
    for r in runs:
        t = r["text"].strip()
        if re.fullmatch(r"\d{1,2}", t):
            number = int(t)
            break
    return {"title": title, "number": number}


def extract_chapter_meta(doc: "pymupdf.Document", exclude_titles: set[str] | None = None) -> dict:
    """Chapter number + title from the PDF's own content — never the
    filename (PLAN.md section 4's source-file naming caveat).

    exclude_titles: disqualifies these exact strings from the tier-1
    repeated-running-header vote (falling through to tiers 2/3 instead).
    Needed for books (found in Social Science) whose running header on
    every page is the *book* title, not the chapter title — that header
    is real, frequent, and title-shaped, so nothing in this function
    alone can tell it apart from a real per-chapter running header. The
    caller (a cross-chapter pass) detects it by noticing the same title
    winning for multiple different chapters in one book and re-extracts
    those chapters with it excluded."""
    from collections import Counter

    # Case-insensitive: the same recurring label can render with
    # different capitalization across tiers on the *same* page (found in
    # production — a color-banner ALL-CAPS source converts to headline
    # case, "Notes for the Teacher", while a small-print running header
    # elsewhere on the same page is already mixed-case as printed,
    # "Notes For The Teacher", every word capitalized) — an exact-string
    # exclude set misses the second rendering entirely.
    exclude_titles_lower = {x.lower() for x in (exclude_titles or set())}

    # Tier 0: the color-banner signal (see _color_banner_title_and_number)
    # — tried first since it's the most direct signal (a title-specific
    # display color/font chosen by the book's actual design, rather than
    # inferring "this is probably the title" from repetition or size
    # alone) and, empirically, the most broadly reliable one found so
    # far. Purely additive: falls through to the tiers below untouched
    # when it finds nothing (or its finding is excluded). Doesn't return
    # early even on a hit: banner_number is None whenever this page's
    # banner didn't separately render a bare chapter-number run, and the
    # existing page-0 big-text pass below is still the mechanism for
    # that — this just pins the title so tiers 1-3 don't override it.
    banner = _color_banner_title_and_number(doc[0])
    banner_title = banner["title"] if banner["title"] and banner["title"].lower() not in exclude_titles_lower else None
    banner_number = banner["number"]

    # A real running header repeats on most pages throughout the chapter.
    # Requiring just "seen >=2 times in the first few pages" isn't enough
    # to rule out a coincidental repeat — e.g. a small worksheet table's
    # column-header text, jumbled by reconstruct_lines' row-based reading
    # order (same class of bug as task 5b's table-linking finding), can
    # satisfy that within just 2-3 nearby pages. Requiring the candidate
    # to appear on a real fraction of the WHOLE chapter's pages is a much
    # stronger, more reliable signal.
    header_pages: dict[str, set[int]] = {}
    for page_num, page in enumerate(doc):
        for line in reconstruct_lines(page):
            t = _collapse_repeated_runs(line["text"].strip())
            # "|" marks a series/book-branding running header (e.g.
            # "Ganita Prakash | Grade 6") rather than a chapter title —
            # those can appear on MORE pages than the actual chapter-title
            # header (task 6 finding: 6 pages vs 5 here), so frequency
            # alone doesn't separate them; exclude the pattern outright.
            if (
                3 <= len(t) <= 60
                and line["size"] <= 11
                and not t[0].isdigit()
                and "|" not in t
                and t.lower() not in _SKIP_HEADER_TEXT
                and not _REPRINT_WATERMARK_RE.match(t)
            ):
                # A running header's page number can be glued directly onto
                # its trailing edge with no separator ("AGRICULTURE31",
                # "AGRICULTURE33", ...) — found in production across an
                # entire book (class 10 Social Science's Geography
                # sub-book), where every chapter's real title repeated
                # correctly but never accumulated a repeat count because
                # each occurrence carried a different page number and so
                # looked like a unique string. Normalize before grouping;
                # the trailing digits carry no title information anyway.
                key = re.sub(r"\d+$", "", t).strip() or t
                header_pages.setdefault(key, set()).add(page_num)

    # The same character-dedup mechanism that drops a leading character
    # from a repeated word ("MONEY" -> a later copy losing its "M") can
    # do the same to a header that never repeats character-for-character
    # at all — a *different* rendering of the same title elsewhere on
    # the page comes out one character short ("EVELOPMENT" alongside a
    # separate, correct "DEVELOPMENT"-containing candidate). Whenever one
    # candidate equals another candidate with its first character
    # removed, they're the same title garbled two different ways — merge
    # the shorter one's pages into the fuller one and drop the shorter
    # key, so frequency/selection never has to choose the truncated one.
    # Deliberately exact-one-character-only, not a general suffix match:
    # a broader "ends with" check was tried and reverted — an ordinary
    # body sentence that coincidentally ends in the same word as the
    # real title ("Solution: We are given two concentric circles" ending
    # in "circles") kept absorbing real single-word titles into a
    # candidate that then failed shape-check itself, orphaning the real
    # title's page count for nothing.
    _all_keys_snapshot = list(header_pages)
    _merge_pairs = [
        (k, next(f for f in _all_keys_snapshot if f != k and f[1:] == k))
        for k in _all_keys_snapshot
        if any(k == f[1:] for f in _all_keys_snapshot if f != k)
    ]
    for shorter, fuller in _merge_pairs:
        if shorter in header_pages and fuller in header_pages:
            header_pages[fuller] |= header_pages.pop(shorter)

    def _big_font_title(lines: list[dict]) -> str | None:
        # A page-0 decorative chapter-number digit ("3", "8") is often
        # rendered far larger than the real title itself — a huge
        # background numeral the title sits in front of or beside (found
        # in production across several class 6-8 books: a 72pt bare digit
        # vs. a 23-30pt real title). Computing max_size from every line
        # including that digit pushes the 0.7 threshold above the title's
        # own size, so this fallback finds nothing even though the title
        # is plainly the largest real text on the page. Bare-digit lines
        # are never title text anyway (same reasoning as tier 0's
        # structural-token exclusion) — drop them before computing the
        # threshold, not just when assembling title text below. A
        # single-character line is excluded on the same reasoning even
        # when it isn't a recognizable digit: found in production, one
        # book's decorative numeral renders through a custom drop-cap
        # font whose character code doesn't map to a normal ASCII digit
        # at all ('6' extracts as an unrelated non-Latin codepoint) — the
        # digit-shape regex misses it, but "a lone character fills this
        # whole giant line by itself" is the same tell regardless of what
        # that character decodes to, and a real title is never one
        # character long anyway (already true of the parts filter below).
        sized_lines = [l for l in lines if len(l["text"].strip()) > 1 and not re.fullmatch(r"\d{1,2}", l["text"].strip())]
        if not sized_lines:
            return None
        max_size = max(l["size"] for l in sized_lines)
        big = [l for l in lines if l["size"] >= max_size * 0.7]
        parts = []
        for l in sorted(big, key=lambda l: l["bbox"][1]):
            raw = l["text"]
            # A leading digit run is usually a glued chapter number
            # ("1PATTERNS IN" -> "PATTERNS IN"), but not when it's really
            # the start of an ordinal date range in the title itself
            # ("11thand 12th Centuries" — found in production: a History
            # chapter's own source PDF prints the ordinal suffix with no
            # space before the next word). Distinguish by case: this
            # corpus always prints a real ordinal suffix in lowercase
            # ("11th", never "11TH"/"11Th"), while a glued chapter number
            # is always followed by the start of the title proper, which
            # in every design this corpus uses is never a lowercase "th"/
            # "st"/"nd"/"rd" at that exact position. Only strip when the
            # text right after the digits does NOT start with one of
            # those four lowercase suffixes.
            digit_match = re.match(r"^(\d+)(.*)$", raw)
            if digit_match and not digit_match.group(2).startswith(("st", "nd", "rd", "th")):
                t = digit_match.group(2)
            else:
                t = raw
            t = t.strip()
            # single-letter fragments are almost always a decorative
            # drop-cap ("C" from a big stylized "CHAPTER", the rest of
            # which renders too small to pass the size filter here)
            if not t or len(t) <= 1 or t.upper() == "CHAPTER":
                continue
            # A bare "SECTION" divider label (optionally with a roman
            # numeral, on either side, and possibly its own separate
            # line — found in production, class 10 Social Science's
            # History sub-book: "SECTION" + "I" as two lines, "SECTION
            # II" as one) is a multi-chapter book's structural page
            # marker, not title text — same reasoning as the bare
            # "CHAPTER" skip just above.
            if re.fullmatch(r"(?:[IVXLCDM]+\s+)?SECTION(?:\s+[IVXLCDM]+)?\.?", t, re.IGNORECASE):
                continue
            # A "Reprint <year>" printer's watermark (found in production,
            # class 10 Social Science's History sub-book: printed on
            # every page, including otherwise-blank ones) is real
            # title-shaped text by every check above, and wins outright
            # on a page with nothing else on it at all — never legitimate
            # title text regardless of size or position.
            if re.fullmatch(r"Reprint\s+\d{4}(?:-\d{2,4})?", t, re.IGNORECASE):
                continue
            # A recurring section-feature label sharing this page with
            # the real title (e.g. "Big Questions", split across two
            # font sizes: "Big" at 23pt clears this tier's threshold,
            # "Questions" at 18pt doesn't) can leave just its first
            # word bleeding into the join ("Landforms and Life Big") —
            # found in production once the cross-chapter pass excluded
            # the label's full text but this per-line scan still only
            # ever sees the fragment, never the whole excluded phrase.
            # Drop a line that's a whole-word-boundary prefix of (or
            # equal to) any excluded title.
            tl = t.lower()
            if any(x == tl or x.startswith(tl + " ") for x in exclude_titles_lower):
                continue
            parts.append(t)
        candidate = " ".join(parts) or None
        # Safety cap (found in production: a Social Science book whose
        # page-0 layout is a large-font decorative "story hook"
        # paragraph rather than a title — every line in it passes the
        # same size filter, so `parts` becomes the whole paragraph
        # instead of a short heading). A title this long is never
        # real; fall through to "Unknown"/tier 3 rather than surface
        # a paragraph as a chapter name.
        if candidate and len(candidate) <= 80 and len(candidate.split()) <= 10 and candidate.lower() not in exclude_titles_lower:
            return candidate.title() if candidate.isupper() else candidate
        return None

    lines0 = reconstruct_lines(doc[0])
    # Computed up front (not just as a fallback tier below) so tier 1's
    # frequency-based pick can be cross-checked against it: this book's
    # own running-header text (see below) isn't a reliable source of
    # truth here.
    page0_title_candidate = _big_font_title(lines0) if lines0 else None
    # A page-0 "SECTION [roman numeral]" marker means this page is a
    # multi-chapter book's part/unit divider (found in production, class
    # 10 Social Science's History sub-book), not this chapter's own title
    # page — `_big_font_title` still returns a candidate for it (the
    # unit's own descriptive theme name, e.g. "EVENTS AND PROCESSES",
    # which is real title-shaped text and a reasonable fallback for a
    # book with no better signal), but here a real per-chapter title
    # exists further in, so that candidate must not be allowed to win —
    # treat page 0 as if it had found nothing, cascading to page 1 then
    # page 2 below instead.
    page0_is_section_divider = any(
        re.fullmatch(r"(?:[IVXLCDM]+\s+)?SECTION(?:\s+[IVXLCDM]+)?\.?", l["text"].strip(), re.IGNORECASE)
        for l in lines0
    )
    if page0_is_section_divider:
        page0_title_candidate = None
    page1_title_candidate = _big_font_title(reconstruct_lines(doc[1])) if len(doc) > 1 else None

    title = banner_title
    # Shape-filtered first: a short recurring artifact (e.g. "abc", "i.e.,"
    # from a worked example that happens to repeat elsewhere in the
    # chapter) can hit the same repeat-count as, or higher than, the real
    # title and would otherwise win on frequency alone (found in
    # production: "abc" beat a title appearing only once). Restricting the
    # candidate pool to title-shaped text first, then taking the most
    # frequent survivor, keeps frequency as the tiebreaker without letting
    # it override shape.
    if title is None:
        title_shaped = {t: pages for t, pages in header_pages.items() if t.lower() not in exclude_titles_lower and _looks_like_title_candidate(t)}
        if title_shaped:
            best_text, pages = max(title_shaped.items(), key=lambda kv: len(kv[1]))
            # Floor of 2, not 3: a short chapter (this corpus goes as low
            # as 6 pages) can have as few as 2 even/odd-page slots for its
            # own running header before the chapter ends (found in
            # production: a real title appearing on exactly pages 2 and 4
            # of a 6-page chapter). Shape-filtering already ran above, so
            # frequency here is a tiebreaker among already-title-shaped
            # candidates, not the sole signal — a 2x coincidental repeat
            # of shaped-but-wrong text is a much smaller residual risk
            # than requiring 3+ and losing genuine short-chapter titles.
            if len(pages) >= max(2, len(doc) // 4):
                # A running header can repeat a title that's missing its
                # own leading character on EVERY occurrence — not a
                # one-off render glitch but this book's header text
                # itself being generated corrupted (found in production:
                # a Social Science sub-book's "DEVELOPMENT" header prints
                # as "EVELOPMENT" on all 7 pages it appears on; the intact
                # word never appears as a running header anywhere in the
                # book, only in body prose and the page-1 opener banner).
                # No amount of frequency-based voting among running
                # headers alone can recover the dropped letter — but the
                # page-0/1 banner scan above sees the same title rendered
                # cleanly at full size. Prefer that fuller reading
                # whenever it's an exact single-leading-character match,
                # the same narrow tell already used for the header-vs-header
                # merge above, just cross-checked against the banner tier
                # too instead of only other running-header candidates.
                fuller = next(
                    (c for c in (page0_title_candidate, page1_title_candidate) if c and c.lower()[1:] == best_text.lower()),
                    None,
                )
                if fuller:
                    title = fuller
                else:
                    # This tier historically only ever won with already
                    # mixed-case running-header text, so it never needed
                    # its own case handling — the recent digit-suffix-
                    # stripping and lower repeat-floor fixes above now let
                    # genuinely ALL-CAPS headers ("REAL NUMBERS", "CIRCLES")
                    # win here too, exposing the gap ("REAL NUMBERS"
                    # regressed from correctly headline-cased to raw
                    # upper-case once it started winning here instead of
                    # falling through to tier 3, which already had this
                    # conversion).
                    title = _to_headline_case(best_text) if best_text.isupper() else best_text

    number = None
    if lines0:
        # A page-0 decorative chapter-number digit ("3", "8") is often
        # rendered far larger than the real title itself, which would
        # otherwise blow out the big-font-title threshold — see
        # _big_font_title's own docstring-equivalent comment above for
        # why that tier excludes it. Chapter-number extraction has the
        # same problem in reverse (it specifically WANTS that digit), so
        # it stays a separate, un-filtered scan over lines0 rather than
        # reusing _big_font_title's filtered pool.
        sized_lines = [l for l in lines0 if len(l["text"].strip()) > 1 and not re.fullmatch(r"\d{1,2}", l["text"].strip())]
        max_size = max((l["size"] for l in sized_lines), default=max(l["size"] for l in lines0))
        big = [l for l in lines0 if l["size"] >= max_size * 0.7]
        for l in big:
            # the chapter number is sometimes its own line ("1"), sometimes
            # glued to the start of the title text with no space
            # ("1PATTERNS IN" — task 6 finding on the class 6 maths book)
            m = re.match(r"^(\d+)", l["text"].strip())
            if m:
                number = int(m.group(1))
                break
        if title is None:
            title = page0_title_candidate

    if title is None and len(doc) > 1:
        # Some chapter-opener layouts push the real title to page 1
        # instead of page 0 — found in production: a Social Science
        # sub-book (Economics) whose page 0 is a "Notes for the Teacher"
        # preface (itself a recurring cross-chapter label, excluded by
        # the caller) with no real title anywhere on it, while page 1
        # carries a clean, large, correctly-spelled title ("DEVELOPMENT"
        # at 46pt) that this tier never looked at because it only ever
        # considered doc[0]. Reusing the same big-font logic one page
        # later recovers it without needing anything book-specific.
        title = page1_title_candidate

    if title is None and len(doc) > 2:
        # A part/unit divider page (page 0) followed by a genuinely
        # blank page 1 (found in production, class 10 Social Science's
        # History sub-book: "SECTION I" + a theme name on page 0, an
        # empty page 1, and the chapter's own real title only on page
        # 2) pushes the title one page further still. Same reasoning as
        # the page-0 -> page-1 fallback just above, one more hop.
        title = _big_font_title(reconstruct_lines(doc[2]))

    if title is None and header_pages:
        # Neither tier above found anything: the running-header check
        # requires repeating across a real fraction of the chapter's
        # pages, which fails for a book that only prints the title once,
        # on its opening page or the page right after it (found in
        # production: NCERT's revised class 10 Maths book renders the
        # title in a small font next to a huge decorative chapter-number
        # digit that eats all of page 0, so the title header appears
        # starting on page 1 instead — and even where it is on page 0,
        # it's often too small a font to pass the page-0 big-font tier
        # above). Re-check page-0-or-1 candidates from that same scan
        # with the shape filter instead of a repeat-count, since a
        # sentence fragment sharing the page ("two positive integers.")
        # would otherwise be indistinguishable from a title by frequency
        # alone. Page 0 is preferred over page 1 when both have a
        # candidate; ties broken by longest text (the fuller heading).
        early_candidates = [
            (t, pages) for t, pages in header_pages.items()
            if (0 in pages or 1 in pages) and t.lower() not in exclude_titles_lower and _looks_like_title_candidate(t)
        ]
        if early_candidates:
            early_candidates.sort(key=lambda tp: (0 not in tp[1], -len(tp[0])))
            best = early_candidates[0][0]
            best = re.sub(r"\d+$", "", best).strip()  # trailing glued chapter number, e.g. "REAL NUMBERS1"
            if best:
                title = best.title() if best.isupper() else best

    final_title = title or "Unknown"
    # A source PDF can embed a literal line-break character inside a
    # colon-subtitle title ("The Gupta Era: An Age of Tireless\nCreativity"
    # as one logical span) — normalize before this becomes a broken TSV
    # row or a chapter name with a raw newline in the middle downstream.
    final_title = re.sub(r"\s+", " ", final_title).strip()
    # A rare source-PDF kerning artifact: a ligature glyph (fi/fl/ffi/...)
    # followed by a literal space before the rest of the word it belongs
    # to ("Reﬂ ections" for "Reflections") — the space is spurious, not a
    # real word break, since a lowercase continuation never legitimately
    # follows one of these mid-word.
    final_title = re.sub(r"([ﬀ-ﬆ])\s+(?=[a-z])", r"\1", final_title)
    # A source PDF can print a superscript ordinal suffix ("th"/"st"/
    # "nd"/"rd") with no space glyph before the next word at all (found
    # in production: a History chapter's own title text, "11th and 12th
    # Centuries", extracts as "11thand 12th Centuries" — the space is
    # genuinely missing in the source, not lost by our extraction).
    # Restricted to right after a digit-plus-ordinal-suffix specifically
    # (not any "th"/"st"/"nd"/"rd" substring) so this never touches an
    # ordinary word that happens to contain one of these letter pairs.
    final_title = re.sub(r"(\d(?:st|nd|rd|th))(?=[a-z])", r"\1 ", final_title)
    # A multi-part book's "[roman numeral] Section" divider label can end
    # up merged onto the front of the real descriptive title it precedes
    # ("Ii Section Livelihoods, Economies and Societies") rather than
    # winning outright as its own candidate (already rejected outright by
    # _looks_like_title_candidate) — strip it as a prefix here too.
    final_title = re.sub(r"^(?:[ivxlcdm]+\s+)?section\s+", "", final_title, flags=re.IGNORECASE).strip()
    # Same idea for a "CHAPTER <marker> : Title" divider page ("CHAPTER
    # I : DEVELOPMENT" -> "DEVELOPMENT") — the marker is a roman numeral
    # or plain number, never part of the actual title.
    final_title = re.sub(r"^chapter\s+[ivxlcdm\d]+\s*:\s*", "", final_title, flags=re.IGNORECASE).strip()
    return {"number": number if number is not None else banner_number, "title": final_title}


def make_chunk_id(class_: str, subject: str, chapter: str, topic_ordinal: int, chunk_index: int) -> str:
    raw = f"{class_}|{subject}|{chapter}|{topic_ordinal}|{chunk_index}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


@functools.lru_cache(maxsize=1)
def _chapter_titles_cache() -> dict[tuple[str, str, str], str]:
    """(class, subject_dir, source_file_stem) -> the cross-chapter-deduped
    correct title, from tasks 13/14's `chapter_titles_final.tsv`."""
    if not _CHAPTER_TITLES_TSV.exists():
        return {}
    cache: dict[tuple[str, str, str], str] = {}
    for line in _CHAPTER_TITLES_TSV.read_text(encoding="utf-8").splitlines():
        cls, subj_dir, stem, _number, title = line.split("\t", 4)
        cache[(cls, subj_dir.lower(), stem)] = title
    return cache


def _known_good_title(class_: str, subject: str, pdf_path: Path) -> str | None:
    """The chapter's own single-PDF extraction (schema.extract_chapter_meta)
    can't tell a book/feature-level recurring label ("Notes for the
    Teacher") from the real title — only a cross-chapter pass sees the
    collision (tasks 13-14's fix_chapter_titles.py, run offline, output
    cached in chapter_titles_final.tsv). Found in production: without
    this, chapter 11's own fresh extraction returns the wrong recurring
    label, so `exclude_titles` never contains "Globalisation and the
    Indian Economy" and a fragment of it ("AND THE INDIAN ECONOMY")
    leaks through topic detection as its own bogus topic — this chapter
    IS in the TSV with the correct title, so preferring it here (only for
    exclude_titles purposes; chunk metadata's own `chapter` field still
    comes from the fresh per-chapter extraction, corrected later by
    `patch_chapter_metadata.py --all`) closes that gap."""
    subject_dir = subject.strip().lower()
    return _chapter_titles_cache().get((class_.strip(), subject_dir, pdf_path.stem))


def build_chunks(pdf_path: Path, class_: str, subject: str, skip_tables: bool = False) -> list[dict]:
    """skip_tables: PyMuPDF's find_tables() costs ~65s on a single
    16-page, diagram-heavy chapter (task 6/10 finding) — fine for one
    subject (task 10), prohibitive across the full corpus (task 11: ~150+
    chapters would run for hours). skip_tables=True leaves has_table
    False everywhere rather than paying that cost at full-corpus scale;
    documented as a known limitation (task 12), not a silent gap.
    """
    doc = pymupdf.open(pdf_path)
    meta = extract_chapter_meta(doc)

    if _uses_revised_curriculum_ss_detector(class_, subject):
        topics, _ = detect_topics_ss_revised_curriculum(pdf_path)
    elif _uses_class10_polsci_detector(class_, subject, pdf_path):
        topics = detect_topics_ss_class10_polsci(pdf_path)
    elif _uses_class10_history_detector(class_, subject, pdf_path):
        topics = detect_topics_ss_class10_history(pdf_path)
    elif _uses_physics_allcaps_detector(class_, subject):
        topics = detect_topics_physics_allcaps_numbered(
            pdf_path,
            end_page=find_appendix_start_page(pdf_path),
            allow_headline_case_fallback=subject.strip().lower() != "biology",
        )
    elif _uses_chem11_ch8_detector(class_, subject, pdf_path):
        topics = detect_topics_chem11_ch8(pdf_path, end_page=find_appendix_start_page(pdf_path))
    else:
        appendix_start = find_appendix_start_page(pdf_path)
        # The chapter's own already-extracted title bleeding into topic
        # detection as its own bogus topic (a fragment of "GLOBALISATION
        # AND THE INDIAN ECONOMY" reading as a real topic) is the ALL-CAPS
        # fallback's problem to guard against — passed through even for
        # books that never need it (numbered-heading books don't reach
        # that fallback at all, so it's simply unused there).
        best_title = _known_good_title(class_, subject, pdf_path) or meta["title"]
        exclude_titles = {best_title} if best_title and best_title != "Unknown" else None
        topics, _ = clean_topics(pdf_path, appendix_start, exclude_titles=exclude_titles)
    chunks = chunk_all(topics)
    chunks = flag_figure_dependent_sentences(chunks)

    # Task 11 finding: some NCERT books reuse a numbering scheme for two
    # genuinely different, ADJACENT sections (observed: a Social Science
    # chapter had two unrelated topics both numbered "2.4", back to back —
    # likely a numbered source-box caption colliding with real section
    # numbering) — using bare topic_number for chunk_id then collides, and
    # comparing to the previous chunk's number can't tell the two apart
    # either, since it never changes between them. chunk_index restarting
    # at 0 is the reliable signal instead: chunk_topics.chunk_all() always
    # starts a new topic's chunks at index 0, regardless of what its
    # printed number is, so counting those resets gives each topic a
    # unique ordinal even when two adjacent topics share a number.
    topic_ordinal = -1
    for c in chunks:
        if c["chunk_index"] == 0:
            topic_ordinal += 1
        c["topic_ordinal"] = topic_ordinal

    figures = link_figures_to_chunks(extract_figures(pdf_path), chunks)

    images_by_chunk: dict[tuple, list[dict]] = {}
    for f in figures:
        if f["linked"]:
            key = (f["topic_number"], f["chunk_index"])
            images_by_chunk.setdefault(key, []).append(
                {"image_file": f["image_file"], "caption": f["caption"]}
            )

    has_table_keys: set = set()
    if not skip_tables:
        page_ranges = topic_page_ranges(pdf_path)
        tables = link_tables_to_chunks(extract_tables(pdf_path), chunks, page_ranges)
        has_table_keys = {(t["topic_number"], t["chunk_index"]) for t in tables if t["linked"]}

    records = []
    for c in chunks:
        key = (c["number"], c["chunk_index"])
        records.append(
            {
                "chunk_id": make_chunk_id(class_, subject, pdf_path.stem, c["topic_ordinal"], c["chunk_index"]),
                "class": class_,
                "subject": subject,
                "source_file": pdf_path.stem,  # reliable resume/skip key — chapter_number is best-effort (task 11 finding: often None)
                "chapter": normalize_topic_display_name(meta["title"]),
                "chapter_number": meta["number"],
                "topic": normalize_topic_display_name(c["name"]),
                "topic_number": c["number"],
                "chunk_index": c["chunk_index"],
                "text": c["text"],
                "has_table": key in has_table_keys,
                "figure_dependent_spans": c["figure_dependent_spans"],
                "images": images_by_chunk.get(key, []),
                "embedding": None,
            }
        )
    return records


def build_reference_corpus_records(pdf_path: Path, class_: str, subject: str) -> list[dict]:
    doc = pymupdf.open(pdf_path)
    meta = extract_chapter_meta(doc)

    if _uses_revised_curriculum_ss_detector(class_, subject):
        # SPEC.md section 6: this book's "Questions, activities and
        # projects" section has no printed answer (open-ended/activity
        # style, unlike the Maths book's Q+Ans. appendix) — captured as a
        # question-WORDING style reference for generation, not a solutions
        # reference, so answer/topic_number/question_number are all None
        # (chapter-level, not tied to any single topic or numbered against
        # a section marker) and source is tagged distinctly so the existing
        # solutions-style-reference query (which needs a real Q+A pair)
        # can keep excluding these.
        _, exercise_questions = detect_topics_ss_revised_curriculum(pdf_path)
        corpus = [
            {"topic_number": None, "question_number": None, "question": q, "answer": None, "source": "ncert_activity"}
            for q in exercise_questions
        ]
    else:
        appendix_start = find_appendix_start_page(pdf_path)
        if appendix_start is None:
            return []
        corpus = extract_reference_corpus(pdf_path, appendix_start)

    for e in corpus:
        e["class"] = class_
        e["subject"] = subject
        e["chapter"] = normalize_topic_display_name(meta["title"])
        e["chapter_number"] = meta["number"]
    return corpus


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) != 4:
        print("Usage: schema.py <path-to-pdf> <class> <subject>")
        sys.exit(1)

    pdf_path = Path(sys.argv[1])
    class_, subject = sys.argv[2], sys.argv[3]

    chunks = build_chunks(pdf_path, class_, subject)
    corpus = build_reference_corpus_records(pdf_path, class_, subject)

    print(f"Chapter: {chunks[0]['chapter'] if chunks else '?'} (number={chunks[0]['chapter_number'] if chunks else '?'})")
    print(f"{len(chunks)} chunks, {len(corpus)} reference-corpus entries\n")
    for c in chunks:
        print(
            f"  [{c['topic_number']}]#{c['chunk_index']} {c['topic']!r}  "
            f"has_table={c['has_table']} images={len(c['images'])} fig_dep_spans={len(c['figure_dependent_spans'])} "
            f"({len(c['text'])} chars)"
        )

    OUTPUT_DIR.mkdir(exist_ok=True)
    (OUTPUT_DIR / f"{pdf_path.stem}_schema_chunks.json").write_text(
        json.dumps(chunks, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (OUTPUT_DIR / f"{pdf_path.stem}_schema_corpus.json").write_text(
        json.dumps(corpus, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nWritten: {pdf_path.stem}_schema_chunks.json, {pdf_path.stem}_schema_corpus.json")


if __name__ == "__main__":
    main()
