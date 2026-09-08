"""Task 3: detect topic-level heading boundaries in one sample NCERT chapter
and split its text into (topic_name, topic_text) pairs.

Scope: only single-level "N.M Heading" boundaries (matching the chapter's
own top-level TOC entries, e.g. "1.1 Chemical Equations") are treated as
topic boundaries here. Deeper "N.M.K" sub-subsection headings stay inside
their parent topic's text (long-topic splitting is task 5). Validating
detected topics against the chapter's actual printed index is task 4 —
this task is a first working cut on one sample chapter.

Real-PDF finding this had to work around: NCERT fakes bold on headings by
rendering the same line 2-3 times at a ~0.2-0.8pt offset, and each render
pass fragments into spans differently — so neither raw dict-mode spans nor
plain-text mode give clean, non-duplicated heading text, and even a
span-level merge misreads a genuinely-overlapping-but-distinct span as a
duplicate (or vice versa) often enough to drop or double a character.
reconstruct_lines() instead works at the character level (rawdict), where
"duplicate" has an unambiguous meaning: the same glyph at a near-identical
position. Two adjacent real occurrences of the same letter are never this
close together (task 4 finding) — real characters need a full glyph width,
duplicate-render offsets stay under 1pt — so this is a safe boundary.

Usage: .venv/Scripts/python scripts/detect_headings.py <path-to-pdf>
"""

import functools
import json
import re
import sys
from collections import Counter
from pathlib import Path

import pymupdf
import wordninja

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"

# A topic heading looks like "1.1 CHEMICAL EQUATIONS" — chapter.section,
# exactly one dot, so "1.1.1 Writing a Chemical Equation" (a sub-subsection,
# not a topic boundary at this level) does not match: the trailing "\s+"
# requires whitespace right where "1.1.1" instead has another digit.
# The trailing "\.?" (found in production, class 9 Maths ch2: "2.4.
# Linear growth and linear decay") tolerates a book that inconsistently
# punctuates its own heading numbers with a trailing period — without it,
# that one heading silently failed to match at all and its content
# merged into the previous topic. This doesn't reopen the door to
# decimal-number false positives (see _numbered_heading_size below):
# those still have to clear the per-chapter calibrated heading SIZE, not
# just this shape, to be accepted as a real topic.
#
# Class 11 Maths audit (2026-09-03): this book's PDF sometimes glues the
# heading number straight onto its title with no space at all
# ("10.1Introduction"), and once even inserts a stray space INSIDE the
# number itself ("10. 5Ellipse") — both are kerning artifacts of how this
# particular PDF was typeset, not a new heading convention. The mandatory
# `\s+` before the title silently dropped every one of these (the
# heading simply never matched, so its content merged into the previous
# topic and every later heading in the chapter went missing too, since
# nothing restarted `current`). Fixed by making the separators tolerant:
# `\s*` between the two numbers absorbs the stray inserted space, and
# the title boundary accepts either real whitespace OR a zero-width
# transition straight into an uppercase letter (real heading titles are
# always Title Case, so this can't be confused with a decimal number
# rolling into more digits or symbols).
#
# Class 11 Chemistry audit (2026-09-03): this book's own glued-heading
# quirk isn't even consistently uppercase after the number — its small-
# caps rendering glitch sometimes lowercases the very first letter too
# ("8.2tetraValence", "8.8methOds" — real headings, no space, lowercase
# start). Widening this to accept ANY letter here was tried and reverted:
# a chemistry chapter's body prose is full of measurements immediately
# followed by a lowercase unit ("41.9 mLof Nitrogen..."), and those
# proved far MORE numerous than this one chapter's real glued-lowercase
# headings, dominating the per-chapter size/number calibration and
# actively making chapter 8 worse, not better. Left as a known, narrower
# limitation (documented in PARSING_EXCEPTIONS.md) rather than reopening
# this shared regex to a much larger false-positive class corpus-wide.
HEADING_RE = re.compile(r"^(\d+)\.\s*(\d+)\.?(?:\s+|(?=[A-Z]))(.{2,100})$")

# Rule 3 follow-up (2026-09-02, Science/Maths audit): some NCERT numbered
# headings ("5.4 [glyph]Parallel and Perpendicular Lines...") have a
# decorative "activity" icon glyph immediately after the number, encoded
# in the PDF's own text stream as a bare control character (found: \x07) —
# not whitespace, so it survives into the captured heading name unless
# stripped explicitly.
_HEADING_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f]+")

# Class 11 Chemistry audit (2026-09-03): three chapters (7, 8, 9 —
# Redox Reactions, Organic Chemistry, Hydrocarbons) have a genuine font-
# embedding defect in the source PDF itself, not an extraction-logic
# bug: a chunk of the SAME named font ("Bookman-Light") used elsewhere on
# the very same page decodes to readable text ("organic chemistry –
# some basic principles...") while OTHER spans in that identical font
# decode to substituted-alphabet garbage ("tKH GHYHORSPHQW RI
# HOHFWURQLF WKHRU\\ RI" for "the development of electronic theory of" —
# each letter consistently shifted by a fixed offset in the alphabet,
# but NOT a uniform byte-level shift: it breaks down for at least one
# letter tested ('y'), and punctuation doesn't follow the same rule
# either). This is consistent with two different font subsets sharing
# one font NAME but embedding different (and in one subset's case,
# broken) CID-to-Unicode mappings — a lower-level PDF production defect
# than anything a text-shape heuristic can safely reverse-engineer.
# Confirmed via a full corpus scan (2026-09-03) that no other chapter
# across all of classes 11-12 exhibits this signature at all; chapter 8
# alone is 41% corrupted line-for-line (every page), chapters 7 and 9
# only 2-4%. Recovering the ORIGINAL text would need decoding the
# specific broken font's own internal glyph table, which isn't available
# from the extracted text alone — guessing a substitution risks feeding
# quiz generation subtly WRONG scientific content, which is worse than
# including less text. The corrupted lines are detected by this
# signature (raw control characters in running prose, never legitimate
# there) and dropped from the stored chunk text entirely rather than
# risk shipping garbled or guessed content; the readable majority of
# each chapter's text is unaffected and still stored normally.
_CORRUPTED_BODY_LINE_RE = re.compile(r"[\x10-\x1f]")


def _is_corrupted_body_line(text: str) -> bool:
    return bool(_CORRUPTED_BODY_LINE_RE.search(text))


# The same font defect above sometimes corrupts a whole span of LETTERS
# only, with no punctuation caught in the middle to land a control
# character in it at all ("Iq Dq Rujdqlf Uhdfwlrq" — found bleeding into
# a Class 11 Chemistry chapter 8 heading's wrapped second line, since it
# still satisfied the plain "looks headline-cased" shape check) — no
# raw byte signature to catch these by. What DOES tell them apart from
# real English: essentially none of their "words" are real words, where
# genuine chemistry prose — even full of technical terms wordninja's
# general-English corpus doesn't recognise — still has most of its
# ordinary connecting words (the, of, is, in, and, ...) intact.
_WORD_COST = wordninja.DEFAULT_LANGUAGE_MODEL._wordcost


def _looks_like_garbled_text(text: str) -> bool:
    words = [w for w in re.findall(r"[A-Za-z]+", text) if len(w) >= 3]
    if len(words) < 4:
        return False
    recognized = sum(1 for w in words if w.lower() in _WORD_COST)
    return recognized / len(words) < 0.35


def _looks_like_garbled_fragment(text: str) -> bool:
    """Same wordninja-frequency signal as _looks_like_garbled_text, but
    usable on very short (2+ word) fragments -- only safe to call where
    a false positive just means "don't merge this line" (a wrap-merge
    continuation check), never where it would silently drop real body
    content: short legitimate technical fragments ("Nucleophile
    Electrophile") can trip the same low-recognition ratio.
    """
    words = [w for w in re.findall(r"[A-Za-z]+", text) if len(w) >= 3]
    if len(words) < 2:
        return False
    recognized = sum(1 for w in words if w.lower() in _WORD_COST)
    return recognized / len(words) < 0.35


# Minor words a real headline-style title correctly keeps lowercase
# (mirrors schema.py's _TITLE_MINOR_WORDS/_to_headline_case, used for the
# analogous chapter-TITLE small-caps fix — duplicated here in a compact
# form rather than imported, since schema.py imports FROM this module and
# a heading NAME's casing needs are simpler: it never has to handle a
# multi-run banner, just one already-isolated string).
_HEADING_MINOR_WORDS = {
    "a", "an", "the", "of", "in", "on", "and", "or", "to", "for", "at",
    "by", "with", "from", "as", "is", "are", "it", "its", "his", "her",
    "our", "your", "their", "up", "down", "into", "onto", "&",
    "between", "than", "through", "during", "without", "within",
    "after", "before", "under", "over",
}


def _capitalize_first(lower: str) -> str:
    return lower[:1].upper() + lower[1:] if lower else lower


def _to_heading_headline_case(text: str) -> str:
    words = text.split(" ")
    out = []
    for i, w in enumerate(words):
        lower = w.lower()
        core = lower.strip(".,:;!?()'\"")
        if i > 0 and core in _HEADING_MINOR_WORDS:
            out.append(lower)
        elif "-" in lower:
            # A hyphenated compound ("kössel-lewis") needs each half
            # capitalized on its own -- capitalizing just the token's
            # first character leaves everything after the hyphen
            # lowercase ("Kössel-lewis" instead of "Kössel-Lewis").
            out.append("-".join(_capitalize_first(part) for part in lower.split("-")))
        else:
            out.append(_capitalize_first(lower))
    return " ".join(out)


# 2026-09-03, user-reported: topic names display in whatever case
# convention their SOURCE textbook happened to use — ALL-CAPS from
# Physics/Biology's real heading style, Title Case from Maths/most
# Chemistry, and (found investigating this report) an actual PDF
# typesetting artifact where some headings are letter-spaced character by
# character in the source itself ("E Q U I L I B R I U M I N P H Y S I C
# A L PROCESSES" for "Equilibrium in Physical Processes") — the tracking
# is applied uniformly to inter-letter AND inter-word gaps alike, so
# there is no geometric signal left in the PDF to recover word
# boundaries from; wordninja's word-frequency-based segmenter is what
# actually recovers them. This is the final display-formatting pass any
# detector's captured topic name goes through before being stored, so
# "textbook style" reads the same regardless of which detector or source
# convention produced the raw text.
_KNOWN_ACRONYMS = {
    "ac", "dc", "dna", "rna", "mrna", "trna", "rrna", "atp", "adp", "amp",
    "nad", "nadh", "nadp", "nadph", "fad", "fadh", "gdp", "gtp",
    "emf", "lcr", "mtp", "sti", "stis", "std", "stds", "hiv", "aids",
    "iucd", "iupac", "vsepr", "ph", "uv", "ir", "nmr", "co2", "h2o",
    "who", "rch", "bod", "ddt", "pcr", "elisa",
}


def _collapse_letter_spaced_run(text: str) -> str:
    """Glue back together a run of 3+ consecutive single-letter "words"
    (the letter-spacing artifact above) and re-segment it into real
    words via wordninja, rather than leaving it as scattered single
    letters or gluing it into one unreadable blob. A run of fewer than 3
    is left alone — genuine short standalone words ("a", "I") are common
    enough that requiring 3+ in a row is what keeps this from ever
    misfiring on ordinary text."""
    tokens = text.split(" ")
    out = []
    i = 0
    while i < len(tokens):
        j = i
        run = []
        while j < len(tokens) and len(tokens[j]) == 1 and tokens[j].isalpha():
            run.append(tokens[j])
            j += 1
        if len(run) >= 3:
            out.append(" ".join(wordninja.split("".join(run))))
            i = j
        else:
            if tokens[i]:
                out.append(tokens[i])
            i += 1
    return " ".join(out)


def _case_display_word(word: str, is_first: bool) -> str:
    def replace_run(m: re.Match) -> str:
        core = m.group(0)
        lower_core = core.lower()
        if lower_core in _KNOWN_ACRONYMS:
            return core.upper()
        if lower_core in _HEADING_MINOR_WORDS and not is_first:
            return lower_core
        return core[0].upper() + core[1:].lower()

    # [A-Za-z] alone splits an accented name into separate runs around
    # the non-ASCII letter ("KÖSSEL" -> "K" + "Ö" + "SSEL"), so each half
    # gets titlecased on its own and the accented letter is left
    # stranded uppercase in the middle ("KÖssel"). Widening the class to
    # the Latin-1 accented range keeps a name like this as one run.
    return re.sub(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", replace_run, word)


def normalize_topic_display_name(name: str) -> str:
    """Final formatting pass for a topic/chapter name right before it's
    stored — every detector's captured text (ALL-CAPS, Title Case,
    letter-spaced) converges to the same consistent "textbook style"
    here, rather than surfacing whatever casing convention its own
    source book happened to use. A name that's already ALL-CAPS in the
    source gets rewritten to headline case (short domain acronyms like
    "DNA"/"EMF" kept upper via the curated list above, ordinary words
    title-cased); a name that ISN'T uniformly ALL-CAPS is already
    reasonably (title- or sentence-) cased by its own detector and is
    left alone here, aside from the letter-spacing and whitespace
    cleanup that apply either way."""
    if name == "Introduction":
        return name
    name = re.sub(r"\s+", " ", name).strip()
    name = _collapse_letter_spaced_run(name)
    if name.isupper():
        words = name.split(" ")
        out = []
        first_done = False
        for w in words:
            if not re.search(r"[A-Za-z]", w):
                out.append(w)
                continue
            out.append(_case_display_word(w, not first_done))
            first_done = True
        name = " ".join(out)
        name = re.sub(r"([’'])S\b", r"\1s", name)
    name = re.sub(r"\s+([?:;,.])", r"\1", name)
    return name


_ACCENTED_UPPER_RE = re.compile(r"[A-Z][À-ÖØ-Þ][a-z]")


def _has_corrupted_midword_capitals(text: str) -> bool:
    """A capital letter directly preceded by a lowercase letter never
    happens in genuine title/heading text — real capitals only ever
    start a word. Found in production (Class 11 Chemistry ch8, same
    small-caps encoding glitch already fixed for this book's chapter
    TITLE, schema.py's _color_banner_title_and_number): "intrOductiOn",
    "rePresentatiOns", "cOmPOunds" — a decorative small-caps font applied
    inconsistently, corrupting SECTION heading names too, not just the
    chapter title.

    Class 11 Chemistry ch4 follow-up (2026-09-03): the same defect can
    also land on a word's SECOND letter when that letter is accented
    ("KÖssel" for "Kössel") -- not preceded by a lowercase letter, so the
    rule above misses it. Narrowly scoped to an accented capital there
    (rather than any second capital) since a genuine two-letter acronym
    prefix followed by lowercase is a real, common shape in this corpus's
    science vocabulary ("ATPase", "DNase") that must not be touched; no
    such term legitimately mixes in an accented letter.
    """
    if any(text[i].isupper() and text[i - 1].isalpha() and text[i - 1].islower() for i in range(1, len(text))):
        return True
    return bool(_ACCENTED_UPPER_RE.search(text))


def _clean_heading_name(name: str) -> str:
    name = _HEADING_CONTROL_CHAR_RE.sub("", name).strip()
    if name.islower() or _has_corrupted_midword_capitals(name):
        name = _to_heading_headline_case(name.lower())
    return name


def _clean_continuation_fragment(text: str) -> str:
    """Like _clean_heading_name, but for a fragment being appended onto
    an EXISTING heading name rather than the heading's own first line.

    Class 11 Chemistry ch5 follow-up (2026-09-03): "of reactions"
    (continuing "5.5 Enthalpies for different types") is plain lowercase
    -- already correctly cased for a mid-title continuation in this
    book's sentence-case heading style. _clean_heading_name's
    name.islower() branch is meant for a heading's own FIRST line (where
    genuinely-lowercase extracted text does need headline-casing to read
    as a title); applying it here instead re-titles the fragment as if
    it were standalone, capitalizing "Of" and "Reactions" as though this
    were "Of Reactions" rather than one clause of a longer title. Only
    run the corruption-repair path (mid-word-capital defects), and leave
    ordinary lowercase text exactly as extracted.
    """
    name = _HEADING_CONTROL_CHAR_RE.sub("", text).strip()
    if _has_corrupted_midword_capitals(name):
        name = _to_heading_headline_case(name.lower())
        first, _, rest = name.partition(" ")
        if first.strip(".,:;!?()'\"").lower() in _HEADING_MINOR_WORDS:
            first = first.lower()
        name = first if not rest else f"{first} {rest}"
    return name

# Loosely matches any numbered heading, including deeper "1.3.1 Corrosion"
# sub-subsections — used to stop a heading-title line-wrap merge (below)
# from swallowing the next real heading, which is itself numbered but
# doesn't match HEADING_RE's stricter 2-level pattern.
ANY_NUMBERED_HEADING_RE = re.compile(r"^\d+\.\d+")

# Task 11 finding (scaling to the full corpus): Social Science NCERT
# chapters (History/Geography/Civics/Economics) don't use "N.M Heading"
# numbering at all — real subsection titles are plain ALL-CAPS text with
# no numeric prefix (e.g. "LAND UTILISATION", "SOIL AS A RESOURCE"), so
# HEADING_RE matches zero topics and the whole chapter falls into one
# undifferentiated chunk. This is the fallback pattern for that case:
# multi-word (excludes short glued decorative fragments like a stray
# drop-cap), length-bounded, all-uppercase-or-space/punctuation.
ALLCAPS_HEADING_RE = re.compile(r"^[A-Z][A-Z0-9 ,\-&'/]{7,79}$")


# A sequence of isolated single letters separated by whitespace ("W S
# N W") is never a real heading — found in production, class 10 Social
# Science's Geography sub-book: a compass-rose diagram's scattered N/S/E/W
# direction labels, row-clustered by their shared y-position on the
# chapter's own title page into one bogus "heading"-shaped line.
_ISOLATED_LETTERS_RE = re.compile(r"^[A-Z](\s+[A-Z])+$")


def _is_allcaps_heading_candidate(text: str) -> bool:
    stripped = text.strip()
    if _ISOLATED_LETTERS_RE.match(stripped):
        return False
    return bool(ALLCAPS_HEADING_RE.match(text)) and " " in stripped


def _collapse_repeated_runs(s: str) -> str:
    """Some books render a title/footer banner as a decorative
    multi-pass repeat effect (a bold/shadow/emboss look achieved by
    printing the same text 3-5x at slightly different offsets) — found
    in production two ways on the same page of one book: a full,
    untouched repeat ("MONEYMONEYMONEYMONEYMONEY") and a partial one
    where the character-level bold-dedup elsewhere in this pipeline
    only strips the leading edge of each repeat
    ("MONEYONEYONEYONEYONEY"). Both collapse correctly under a plain
    repeated-substring regex — collapse before any shape/candidate
    logic sees this text.

    Requires the repeated unit to be 2+ characters: a 1-character unit
    matches legitimate content too — found in production, "1000" has
    "000" (three zeros) as a real, correct repeat, and collapsed to "10"
    before this guard, corrupting a date ("...up to 1000 CE" -> "...up
    to 10 CE")."""
    return re.sub(r"(.{2,}?)\1{2,}", r"\1", s)


# Task 2 finding: body text tops out at ~10.5pt; headings/callout boxes
# start at 14pt. The bold flag is not usable (NCERT fakes bold via
# overlapping duplicate renders), so size is the confirmation signal.
MIN_HEADING_SIZE = 12.0

# Tolerance (in PDF points) for treating characters as being on the same
# visual row (ROW_TOL) and for treating two same-character glyphs as one
# duplicated by NCERT's bold-render trick rather than two distinct letters
# (CHAR_TOL) — task 3/4 findings: duplicate-pass offsets stay under 1pt,
# while real character widths here run ~7-8pt, so 2pt cleanly separates them.
ROW_TOL = 3.0
CHAR_TOL = 2.0

# Class 12 Physics audit (2026-09-03): this book renders a per-WORD drop
# cap on every numbered heading, not just a title's single leading letter
# ("1.2  E C" at 16pt immediately above "LECTRICHARGE" at 11.2pt, for
# "1.2 ELECTRIC CHARGE") — each significant word's first letter enlarged,
# the enlarged letters and the remainders each landing in their own
# reconstructed row because the drop cap's baseline sits ~3.6pt above the
# remainder's, just outside ROW_TOL (3.0). Raising ROW_TOL itself risks
# merging genuinely distinct lines elsewhere in the corpus; this is a
# narrow, targeted second pass instead — sorting the drop-cap row and its
# very next row together by x0 (rather than concatenating them in
# y0-sorted, i.e. drop-caps-then-remainders, order) reads out correctly
# interleaved text, exactly the way `row.sort(key=lambda c: c["x0"])`
# below already does within a single row. Confirmed by hand against the
# raw character bboxes: "1.2  E" (x=173-276) then "LECTRIC" (x=223-328,
# i.e. immediately right of "E") then " C" (x=276-293) then "HARGE"
# (x=293-...) sorts to "1.2  ELECTRIC CHARGE".
#
# A possessive heading ("Ohm's Law", "Gauss's Law") renders its apostrophe
# at the SAME enlarged size as the drop caps around it, one more oversized
# token interspersed among them ("3.4  O’ L" over "HMSAW" — reads out as
# "O" + "HM" + "’" + "S" + " " + "L" + "AW" = "OHM’S LAW") — and a heading
# naming two things ("Element, Biot-Savart Law", "Semiconductor
# Electronics:") carries a comma/hyphen/colon at that same enlarged size
# too. Enumerating every punctuation mark that can turn up here is a losing
# game; what every one of these rows reliably has in common, and body text
# never does, is zero lowercase letters at all.
_MAX_DROPCAP_INITIALS_ROW_CHARS = 20


def _looks_like_dropcap_initials_row(text: str) -> bool:
    core = re.sub(r"^\d+\.\s*\d+\.?\s*", "", text).strip()
    if not core or len(core) > _MAX_DROPCAP_INITIALS_ROW_CHARS:
        return False
    if any(c.islower() for c in core):
        return False
    return any(c.isalpha() for c in core)


def _dedup_row(row: list[dict]) -> list[dict]:
    """Drop NCERT's duplicate-render-for-bold copies of the same glyph
    from one row, sorted left to right. Split out of `_reconstruct_block_
    lines` so the drop-cap merge pass below can shape-check a row's REAL
    text, not a 5x-inflated one — found in production: "T B M" (this
    book's bold trick renders every character 5 times) is ~50 characters
    un-deduped, well past the drop-cap row length cap, so the merge that
    depends on recognizing it as a drop-cap row never fired until dedup
    ran first.
    """
    row = sorted(row, key=lambda c: c["x0"])
    accepted: list[dict] = []
    for c in row:
        if accepted and accepted[-1]["c"] == c["c"] and abs(accepted[-1]["x0"] - c["x0"]) <= CHAR_TOL:
            continue  # duplicate render of the same glyph
        accepted.append(c)
    return accepted


def _reconstruct_block_lines(chars: list[dict]) -> list[dict]:
    """Row-cluster and de-duplicate one block's characters (see module
    docstring for why this has to happen at the character level)."""
    chars = sorted(chars, key=lambda c: (c["y0"], c["x0"]))

    rows: list[list[dict]] = []
    for c in chars:
        if rows and abs(c["y0"] - rows[-1][-1]["y0"]) <= ROW_TOL:
            rows[-1].append(c)
        else:
            rows.append([c])
    rows = [_dedup_row(row) for row in rows]

    merged_rows: list[list[dict]] = []
    i = 0
    while i < len(rows):
        row = rows[i]
        if i + 1 < len(rows):
            next_row = rows[i + 1]
            row_text = "".join(c["c"] for c in row).strip()
            row_size = max(c["size"] for c in row)
            next_size = max(c["size"] for c in next_row)
            y_gap = next_row[0]["y0"] - row[-1]["y0"]
            if (
                _looks_like_dropcap_initials_row(row_text)
                and row_size > next_size * 1.15
                and 0 <= y_gap <= 8
            ):
                merged_rows.append(sorted(row + next_row, key=lambda c: c["x0"]))
                i += 2
                continue
        merged_rows.append(row)
        i += 1
    rows = merged_rows

    lines = []
    for accepted in rows:
        text = "".join(c["c"] for c in accepted).strip()
        if text:
            lines.append(
                {
                    "text": text,
                    "size": max(c["size"] for c in accepted),
                    "bbox": (
                        min(c["x0"] for c in accepted),
                        min(c["y0"] for c in accepted),
                        max(c["x1"] for c in accepted),
                        max(c["y1"] for c in accepted),
                    ),
                }
            )
    return lines


def reconstruct_lines(page) -> list[dict]:
    """Return this page's visual lines as [{"text", "size"}], with NCERT's
    duplicate-render-for-bold artifact removed (see module docstring).

    Row-clustering runs per PyMuPDF block, not globally across the page
    (task 5 finding): a sidebar/inset box can sit at the same page height
    as the main column's text, and a global y-only clustering merges the
    two into one garbled line just because they share a y-coordinate,
    even though their x-ranges don't overlap at all. Blocks are PyMuPDF's
    own layout segmentation, so keeping row-clustering scoped to one block
    keeps side content from bleeding into the main text.
    """
    lines = []
    for block_num, block in enumerate(page.get_text("rawdict")["blocks"]):
        if block["type"] != 0:  # skip images; task 5a handles those
            continue
        # A page-margin vertical sidebar/spine branding string (e.g.
        # "Curiosity | Textbook of Science | Grade 6") is rendered as
        # individually-placed upright characters stacked down the page,
        # not a rotated text run pymupdf's span direction would flag —
        # each character's own bbox is a normal small upright glyph, just
        # positioned at ~constant x with increasing y. Row-clustering
        # below (grouped by y-proximity) then shreds it into 1-3 char
        # fragments, one of which can coincidentally repeat often enough
        # to win the running-header vote over the real chapter title
        # (found in production: "Curiosity" -> fragment "tis" won across
        # 4/8 pages of a class 6 Science chapter). Geometrically this
        # block is unmistakable regardless of content: much taller than
        # it is wide, and no real paragraph/heading text column is ever
        # this narrow — so skip it outright rather than trying to filter
        # its fragments out downstream by content.
        bx0, by0, bx1, by1 = block["bbox"]
        bw, bh = bx1 - bx0, by1 - by0
        if bw < 20 and bh > 50 and bh > 4 * bw:
            continue
        chars = [
            {
                "c": ch["c"],
                "x0": ch["bbox"][0],
                "y0": ch["bbox"][1],
                "x1": ch["bbox"][2],
                "y1": ch["bbox"][3],
                "size": span["size"],
            }
            for line in block["lines"]
            for span in line["spans"]
            for ch in span["chars"]
        ]
        for line in _reconstruct_block_lines(chars):
            line["block"] = block_num
            lines.append(line)
    return lines


# Class 10 Social Science's Geography sub-book (task 15 follow-up,
# 2026-09-02, found by the user reviewing the app: "this textbook is very
# different... it has two continuous halves in one page, as a reader i
# read first left half and then right"). Confirmed via raw span dumps
# across chapters 1, 2, 4, and 7: every page is laid out as two side-by-
# side text columns (left ~x60-215, right ~x306-534 on a 576pt-wide
# page), and `reconstruct_lines()`'s own PyMuPDF block ordering doesn't
# reliably put the left column's blocks before the right column's — one
# sample page returned the entire right column FIRST, then the entire
# left column, silently reading the page in the wrong order into every
# downstream detector (topic headings, running headers, everything).
# Row-clustering itself is fine (already scoped per-block, so it never
# merges the two columns into one garbled line) — only the ORDER blocks
# are read in was wrong. Explicit chapter-number allowlist, matching
# every other per-textbook design in this module — Economics (chapters
# 8-12, checked in the same pass) uses a normal single, if narrow, text
# column and needs no such reordering.
_GEOGRAPHY_TWO_COLUMN_CHAPTERS = {"1", "2", "3", "4", "5", "6", "7"}


def _is_class10_geography_pdf(pdf_path_str: str) -> bool:
    path = Path(pdf_path_str)
    if "social science" not in {p.lower() for p in path.parts}:
        return False
    if not any(p.strip() == "10" or p.strip().endswith("class 10") for p in path.parts):
        return False
    m = re.search(r"\d+", path.stem)
    return bool(m) and m.group() in _GEOGRAPHY_TWO_COLUMN_CHAPTERS


def _reorder_two_column_page(lines: list[dict], page_width: float) -> list[dict]:
    """Re-groups a page's lines by their PyMuPDF block, classifies each
    block as left- or right-column by its own average x-position, then
    emits every left-column block (in original relative order) before
    every right-column block (in original relative order) — Python's
    sort is stable, so this is the only change made to reading order.
    A block straddling the midpoint (a running header/footer, a full-
    width caption) lands wherever its average x happens to fall; minor
    and harmless, since it's never real paragraph content."""
    if not lines:
        return lines
    mid_x = page_width / 2
    by_block: dict[int, list[dict]] = {}
    block_order: list[int] = []
    for line in lines:
        b = line["block"]
        if b not in by_block:
            by_block[b] = []
            block_order.append(b)
        by_block[b].append(line)

    def column_of(block_num: int) -> int:
        block_lines = by_block[block_num]
        avg_x = sum((l["bbox"][0] + l["bbox"][2]) / 2 for l in block_lines) / len(block_lines)
        return 0 if avg_x < mid_x else 1

    reordered = []
    for b in sorted(block_order, key=column_of):
        reordered.extend(by_block[b])
    return reordered


@functools.lru_cache(maxsize=None)
def all_pages_lines(pdf_path_str: str) -> tuple:
    """reconstruct_lines() for every page, computed once per PDF and
    cached for the process's lifetime.

    Performance finding (task 10 prep, scaling to a full subject):
    detect_topics(), topic_page_ranges(), clean_topics(), and
    extract_figures() each independently opened the PDF and re-ran
    reconstruct_lines() (rawdict extraction + character-level dedup) over
    every page — the same page parsed 3-4x. That alone cost ~45s on one
    16-page chapter. Every one of those call sites now goes through this
    shared cache instead of calling pymupdf.open()+reconstruct_lines()
    itself, cutting that to a single pass per PDF.
    """
    doc = pymupdf.open(pdf_path_str)
    two_column = _is_class10_geography_pdf(pdf_path_str)
    pages = []
    for page in doc:
        lines = reconstruct_lines(page)
        if two_column:
            lines = _reorder_two_column_page(lines, page.rect.width)
        pages.append(tuple(lines))
    return tuple(pages)


# Task 15 follow-up (class 10 Social Science, the older ALL-CAPS design):
# structural/exercise-box labels found in production winning outright as
# topics via the shape check above — a bare "CHAPTER I"/"CHAPTER 2"
# divider, an "ACTIVITY"/"ACTIVITY 1" exercise-box marker, an "Additional
# Project / Activity" label, and the Economics sub-book's recurring
# "Notes for the Teacher" feature box (the same label already known to
# need exclusion at the chapter-TITLE level — see schema.py's chapter-
# title work — but topic detection had no equivalent exclusion at all).
_STRUCTURAL_TOPIC_LABEL_RE = re.compile(
    r"^CHAPTER\s+[IVXLCDM\d]+$"
    r"|^ACTIVITY(\s+\d+)?$"
    r"|^ADDITIONAL\s+PROJECTS?\s*/\s*ACTIVIT(Y|IES)$"
    r"|^NOTES\s+FOR\s+(THE\s+)?TEACHERS?$",
    re.IGNORECASE,
)
# "SUMMING UP" is a whole-chapter recap restating facts across several
# topics in one paragraph (same non-attributable-to-one-topic reasoning as
# the revised-curriculum books' "Before we move on"), and "EXERCISES"
# marks this book's own end-of-chapter exercise questions — found in
# production flowing directly into whatever topic was open when
# encountered (fill-in-the-blank and MCQ exercise text ending up as
# grounding content), since "EXERCISES" alone doesn't have the space
# _is_allcaps_heading_candidate requires and so was never even recognized
# as a boundary at all. Whichever is seen first ends real topic content
# for the rest of the chapter.
_CHAPTER_END_RE = re.compile(r"^(SUMMING UP|EXERCISES)$", re.IGNORECASE)
# A "TABLE N.M Caption Text" heading (found in production, class 10
# Social Science's Economics sub-book, "Development" chapter) renders at
# the exact same size as a real topic heading. The line itself never
# passes _is_allcaps_heading_candidate (its "N.M" always has a literal
# decimal point, outside the allowed character class) and so correctly
# falls through as body text on its own — but its WRAPPED CONTINUATION
# line ("CATEGORIES OF PERSONS" completing "TABLE 1.1 DEVELOPMENTAL GOALS
# OF DIFFERENT") has no number or period of its own, passes the shape
# check cleanly, and was winning outright as its own bogus topic. Once a
# table-caption start is seen, every following still-heading-sized line
# is suppressed as part of the same caption until a normal body-sized
# line signals the caption (and, usually, the table under it) has ended.
_TABLE_CAPTION_START_RE = re.compile(r"^TABLE\s+\d", re.IGNORECASE)


def _detect_topics_allcaps_fallback(pdf_path: Path, exclude_titles: set[str] | None = None) -> list[dict]:
    """Same shape as detect_topics()'s main loop, keyed on ALLCAPS_HEADING_RE
    instead of numbered headings — see that regex's comment. Topics get
    sequential synthetic numbers ("1", "2", ...) since there's no natural
    numbering scheme to reuse; still unique per chapter for chunk_id
    purposes (schema.py includes chapter + topic_number + chunk_index).

    exclude_titles: the chapter's own already-extracted title (schema.py's
    extract_chapter_meta) — found in production bleeding into topic
    detection as its own bogus "topic" ("GLOBALISATION AND THE INDIAN
    ECONOMY" splitting into a fragment "AND THE INDIAN ECONOMY" that then
    read as a real topic). Can't just skip page 0/1 wholesale the way the
    revised-curriculum detector does: several real topics in this older
    design genuinely start on page 0 or 1 (e.g. "TYPES OF FARMING",
    "MONEY AS A MEDIUM OF EXCHANGE"), so the exclusion has to be specific
    to the chapter's own title text, not a blanket page cutoff."""
    exclude_titles_lower = {x.lower() for x in (exclude_titles or set())}
    all_lines = all_pages_lines(str(pdf_path))

    def _normalize(text: str) -> str:
        # A decorative repeated banner ("EXERCISES EXERCISES EXERCISES...")
        # collapses to one word first; a book that renders a heading as a
        # multi-pass duplicate-offset effect (found in production: "MONEY"
        # -> "MONEYONEYONEYONEYONEY") needs the character-level collapse
        # too, since word-splitting alone can't see inside one run-together
        # "word" like that.
        words = text.split()
        collapsed = " ".join(dict.fromkeys(words)) if words and len(set(words)) == 1 else text
        return _collapse_repeated_runs(collapsed)

    # First pass: a running header repeats across a real fraction of the
    # WHOLE chapter's pages — found in production, a page number glued
    # directly onto the header's trailing edge with no separator
    # ("SECTORS OF THE INDIAN ECONOMY21", "...23", "...25"...) made every
    # occurrence look like a unique heading and thus its own topic, the
    # exact bug already found and fixed for chapter-TITLE extraction
    # (schema.py's header_pages) but never ported to topic detection.
    # Digit-suffix-stripping before counting recovers the real repeat
    # count; same page-fraction threshold as that fix.
    header_pages: dict[str, set[int]] = {}
    for page_num, page_lines in enumerate(all_lines):
        for line in page_lines:
            collapsed = _normalize(line["text"])
            if _is_allcaps_heading_candidate(collapsed) and line["size"] >= MIN_HEADING_SIZE:
                key = re.sub(r"\d+$", "", collapsed).strip() or collapsed
                header_pages.setdefault(key, set()).add(page_num)
    running_headers = {k for k, pages in header_pages.items() if len(pages) >= max(2, len(all_lines) // 4)}

    topics = []
    intro_lines = []
    current = None
    chapter_ended = False
    in_table_caption = False
    seen_title_counts: dict[str, int] = {}
    # A heading can wrap onto a second line ("INTERLINKING PRODUCTION
    # ACROSS" / "COUNTRIES", "FOREIGN TRADE AND INTEGRATION" / "OF
    # MARKETS" — both real, single headings found split across two lines
    # in production) — accumulated here and flushed as one combined name,
    # same "same PyMuPDF block, no restart" wrap-merge signal detect_topics()
    # already uses for numbered headings, ported here since this fallback
    # never had it at all (every multi-line heading was silently becoming
    # two separate bogus topics, one missing the other half of its name).
    pending: dict | None = None

    def _is_title_fragment(name: str, page_num: int) -> bool:
        # A fuzzy, not exact, match: the chapter title itself can split
        # across differently-sized spans on its own banner page ("GLOBALI-
        # SATION" at 48pt, "AND THE INDIAN ECONOMY" at 30pt, in separate
        # blocks) — the fragment that leaks through as a topic candidate is
        # rarely the WHOLE title, just a piece of it (or vice versa, for a
        # short title fully contained in a longer merged candidate).
        #
        # Restricted to page 0/1 (the chapter's own title-page banner,
        # matching the "page0/page1 title candidate" convention used
        # elsewhere in this module) — found in production, chapter 3
        # ("Water Resources"): a genuinely different, real topic several
        # pages in ("Multi-Purpose River Projects and Integrated Water
        # Resources Management") legitimately contains the chapter title
        # as a substring too, purely because it's ABOUT water resources,
        # and was being wrongly dropped as if it were the title itself
        # bleeding through. The actual title-bleed phenomenon only ever
        # happens right on the title page; a topic occurring later that
        # happens to share the title's words is real content, not a
        # rendering artifact.
        #
        # Also skipped entirely when the chapter's own title is just one
        # word (found in production: chapter 8's title, "Development", is
        # generic enough that "National Development"/"Sustainability of
        # Development" — real, later topics — legitimately contain it).
        # A single common word isn't distinctive enough to use this way;
        # the multi-word titles this check exists for ("Globalisation and
        # the Indian Economy") are unambiguous even without the page
        # restriction, but both guards apply together regardless.
        if page_num > 1:
            return False
        name_lower = name.lower()
        return any(
            len(t.split()) > 1 and (name_lower in t or t in name_lower)
            for t in exclude_titles_lower
            if t
        )

    def flush_pending():
        nonlocal current, pending
        if pending is None:
            return
        name = " ".join(pending["parts"])
        if _is_title_fragment(name, pending["page"]):
            pending = None
            return
        key = re.sub(r"\d+$", "", name).strip() or name
        seen_title_counts[key] = seen_title_counts.get(key, 0) + 1
        if seen_title_counts[key] == 1:  # same heading text recurring (e.g. a running header) — not a new topic each time
            if current:
                topics.append(current)
            current = {"number": str(len(topics) + 1), "name": name.title(), "text": []}
        pending = None

    for page_num, page_lines in enumerate(all_lines):
        for line in page_lines:
            if chapter_ended:
                continue

            text, size, block_num = line["text"], line["size"], line["block"]
            if _is_corrupted_body_line(text):
                continue
            collapsed = _normalize(text)
            stripped = collapsed.strip()

            if _CHAPTER_END_RE.match(stripped):
                flush_pending()
                chapter_ended = True
                continue

            if in_table_caption:
                if size >= MIN_HEADING_SIZE:
                    if current:
                        current["text"].append(text)
                    else:
                        intro_lines.append(text)
                    continue
                in_table_caption = False  # back to body-sized text: the caption (and its table) has ended

            if _TABLE_CAPTION_START_RE.match(stripped) and size >= MIN_HEADING_SIZE:
                flush_pending()
                in_table_caption = True
                if current:
                    current["text"].append(text)
                else:
                    intro_lines.append(text)
                continue

            # A genuine wrap continuation ("COUNTRIES" completing
            # "INTERLINKING PRODUCTION ACROSS") is often a single word
            # with no space of its own — found in production failing
            # _is_allcaps_heading_candidate's space requirement on its
            # own, even though the line it's completing passed easily.
            # Matching detect_topics()'s existing wrap-merge for numbered
            # headings: once a pending heading has started, the SAME
            # PyMuPDF block continuing at heading size is accepted as
            # part of it regardless of shape — only a genuinely NEW
            # heading (no pending open, or a different block) needs the
            # full shape check.
            if pending and pending["block"] == block_num and size >= MIN_HEADING_SIZE:
                pending["parts"].append(stripped)
                continue

            key = re.sub(r"\d+$", "", collapsed).strip() or collapsed
            is_candidate = (
                _is_allcaps_heading_candidate(collapsed)
                and size >= MIN_HEADING_SIZE
                and key not in running_headers
                and not _STRUCTURAL_TOPIC_LABEL_RE.match(stripped)
            )

            if is_candidate:
                # pending is never open on the same block here — the
                # continuation branch above already handled that case.
                flush_pending()
                pending = {"block": block_num, "page": page_num, "parts": [stripped]}
                continue

            flush_pending()
            if current:
                current["text"].append(text)
            else:
                intro_lines.append(text)

    flush_pending()
    if current:
        topics.append(current)
    for t in topics:
        t["text"] = "\n".join(t["text"])

    # Same "an unhelpful topic is worse than a missing one" reasoning as
    # the revised-curriculum detector — a structural label that slipped
    # past the shape/exclusion checks above with only a few words of real
    # text before the next heading is still useless for grounding.
    topics = [t for t in topics if len(t["text"].strip()) >= _MIN_SEGMENT_CHARS]
    for i, t in enumerate(topics, start=1):
        t["number"] = str(i)

    intro_text = "\n".join(intro_lines).strip()
    segments = []
    if len(intro_text) >= _MIN_SEGMENT_CHARS:
        segments.append({"number": None, "name": "Introduction", "text": intro_text})
    segments.extend(topics)
    return segments


### Class 10 Social Science's Political Science sub-book (task 15 follow-up,
### 2026-09-02): a THIRD distinct design within the same class 10 Social
### Science corpus, alongside History's own font-size-tiered design (below)
### and the ALL-CAPS Geography/Economics sub-books above — confirming the
### user's original "4 different textbooks, 4 different architectures"
### framing applies within one class's Social Science, not just across
### classes.
### Found via _detect_topics_allcaps_fallback returning zero topics for
### every one of these 5 chapters even though a real, clean signal exists:
### this sub-book's real headings are Title Case, not ALL-CAPS at all (the
### ALL-CAPS fallback's own regex could never have matched them), and are
### font-size-tiered instead — verified across all 5 chapters: a real topic
### renders at 20pt, a subheading within a topic at 13pt, and the
### structural "Overview" label (marking the start of each chapter's own
### intro paragraph) plus occasional table/chart captions render at 18pt,
### safely outside both ranges. No callout-box system and no end-of-chapter
### recap/exercises HEADING exist in this sub-book at all (exercise
### questions simply begin with a numbered list, no label, right after the
### last topic's own content) — a residual, undetectable-without-a-false-
### positive-risk source of minor tail-end noise in the last topic of each
### chapter, accepted rather than chased (see PARSING_EXCEPTIONS.md).
_POLSCI_TOPIC_SIZE_RANGE = (19.5, 20.5)
_POLSCI_SUBHEADING_SIZE_RANGE = (12.5, 13.5)


def detect_topics_ss_class10_polsci(pdf_path: Path) -> list[dict]:
    """Font-size-tiered detector for class 10 Social Science's Political
    Science sub-book (Democratic Politics – II). Same segment shape as the
    other detectors in this module — a subheading is embedded as a
    SUBHEADING_MARKER-prefixed line within its parent topic's text, split
    into its own chunk (chained under the SAME topic) by chunk_topics.py,
    never promoted to a topic of its own."""
    topics: list[dict] = []
    intro_lines: list[str] = []
    current: dict | None = None
    pending: dict | None = None

    def flush_pending():
        nonlocal current, pending
        if pending is None:
            return
        name = " ".join(pending["parts"])
        if pending["tier"] == "topic":
            if current:
                topics.append(current)
            current = {"number": str(len(topics) + 1), "name": name, "text": []}
        else:
            marked = SUBHEADING_MARKER + name
            if current:
                current["text"].append(marked)
            else:
                intro_lines.append(marked)
        pending = None

    for page_lines in all_pages_lines(str(pdf_path)):
        for line in page_lines:
            text, size, block_num = line["text"], line["size"], line["block"]
            if _is_corrupted_body_line(text):
                continue
            stripped = text.strip()
            if not stripped:
                continue

            in_topic_range = _POLSCI_TOPIC_SIZE_RANGE[0] <= size <= _POLSCI_TOPIC_SIZE_RANGE[1]
            in_subheading_range = _POLSCI_SUBHEADING_SIZE_RANGE[0] <= size <= _POLSCI_SUBHEADING_SIZE_RANGE[1]
            is_heading_candidate = (in_topic_range or in_subheading_range) and _looks_like_heading_text(stripped)

            if is_heading_candidate:
                tier = "topic" if in_topic_range else "subheading"
                if pending and pending["tier"] == tier and pending["block"] == block_num:
                    pending["parts"].append(stripped)
                else:
                    flush_pending()
                    pending = {"tier": tier, "block": block_num, "parts": [stripped]}
                continue

            flush_pending()
            if current:
                current["text"].append(text)
            else:
                intro_lines.append(text)

    flush_pending()
    if current:
        topics.append(current)
    for t in topics:
        t["text"] = "\n".join(t["text"])

    topics = [t for t in topics if len(t["text"].strip()) >= _MIN_SEGMENT_CHARS]
    for i, t in enumerate(topics, start=1):
        t["number"] = str(i)

    intro_text = "\n".join(intro_lines).strip()
    segments = []
    if len(intro_text) >= _MIN_SEGMENT_CHARS:
        segments.append({"number": None, "name": "Introduction", "text": intro_text})
    segments.extend(topics)
    return segments


### Class 10 Social Science's History sub-book (task 15 follow-up,
### 2026-09-02, found by the user reviewing the app): this book's numbered
### headings looked superficially like the generic "N.M Heading" design
### `detect_topics()`/`clean_topics()` already handles (both were routing
### it there, unchanged, with no dedicated audit), but the two numbering
### levels are NOT chapter.section — they're section.subsection, and only
### the bare, single-number "N  Title" (18pt) is a real topic; every
### "N.M Title" (12pt) is a SUBHEADING of that section, not its own topic.
### Routing this book through the generic detector was silently promoting
### every subsection to a sibling topic, the exact Rule 1 violation the
### mandatory topic/subtopic rule exists to prevent — confirmed across all
### 5 chapters (6, 4, 4, 6, and 9 real topics respectively, each with its
### own several 12pt subsections). Body text renders at 11.5pt, only
### 0.5pt below the subheading tier, so the subheading size range is kept
### deliberately tight to exclude it (found in production: a body
### sentence stating cloth prices, "54.7 cm of cloth, in Mainz 55.1
### cm...", coincidentally matches the "N.M " shape at body size and would
### otherwise be promoted too, the same class of decimal-number false
### positive already fixed for Maths/Science's shared numbered-heading
### path).
_HISTORY_TOPIC_SIZE_RANGE = (17.5, 18.5)
_HISTORY_SUBHEADING_SIZE_RANGE = (11.8, 12.2)
_HISTORY_TOPIC_RE = re.compile(r"^(\d+)\s+(.{2,100})$")
_HISTORY_SUBHEADING_RE = re.compile(r"^(\d+)\.(\d+)\s+(.{2,100})$")


def detect_topics_ss_class10_history(pdf_path: Path) -> list[dict]:
    """Font-size-tiered detector for class 10 Social Science's History
    sub-book (India and the Contemporary World – II). Unlike the generic
    numbered-heading design, both tiers carry their own explicit number in
    the source text ("2  Title", "2.1 Title") — extracted here rather than
    auto-assigned, so the stored topic_number matches the book's own
    section numbering. A subheading is embedded as a SUBHEADING_MARKER-
    prefixed line within its parent topic's text, split into its own
    chunk (chained under the SAME topic) by chunk_topics.py, never
    promoted to a topic of its own."""
    topics: list[dict] = []
    intro_lines: list[str] = []
    current: dict | None = None
    pending: dict | None = None

    def flush_pending():
        nonlocal current, pending
        if pending is None:
            return
        name = " ".join(pending["name_parts"]).strip()
        if pending["tier"] == "topic":
            if current:
                topics.append(current)
            current = {"number": pending["number"], "name": name, "text": []}
        else:
            marked = SUBHEADING_MARKER + name
            if current:
                current["text"].append(marked)
            else:
                intro_lines.append(marked)
        pending = None

    for page_lines in all_pages_lines(str(pdf_path)):
        for line in page_lines:
            text, size, block_num = line["text"], line["size"], line["block"]
            if _is_corrupted_body_line(text):
                continue
            stripped = text.strip()
            if not stripped:
                continue

            in_topic_range = _HISTORY_TOPIC_SIZE_RANGE[0] <= size <= _HISTORY_TOPIC_SIZE_RANGE[1]
            in_subheading_range = _HISTORY_SUBHEADING_SIZE_RANGE[0] <= size <= _HISTORY_SUBHEADING_SIZE_RANGE[1]

            topic_m = _HISTORY_TOPIC_RE.match(stripped) if in_topic_range else None
            sub_m = _HISTORY_SUBHEADING_RE.match(stripped) if in_subheading_range else None

            if topic_m and _looks_like_heading_text(topic_m.group(2)):
                flush_pending()
                pending = {"tier": "topic", "block": block_num, "number": topic_m.group(1), "name_parts": [topic_m.group(2)]}
                continue
            if sub_m and _looks_like_heading_text(sub_m.group(3)):
                flush_pending()
                pending = {"tier": "subheading", "block": block_num, "number": f"{sub_m.group(1)}.{sub_m.group(2)}", "name_parts": [sub_m.group(3)]}
                continue

            # A heading title wrapped onto a second line: same tier's size
            # range, same block, no leading number of its own (already
            # ruled out above), still real words rather than a decorative
            # all-caps fragment.
            if (
                pending is not None
                and block_num == pending["block"]
                and (
                    (pending["tier"] == "topic" and in_topic_range)
                    or (pending["tier"] == "subheading" and in_subheading_range)
                )
                and _looks_like_heading_text(stripped)
            ):
                pending["name_parts"].append(stripped)
                continue

            flush_pending()
            if current:
                current["text"].append(text)
            else:
                intro_lines.append(text)

    flush_pending()
    if current:
        topics.append(current)
    for t in topics:
        t["text"] = "\n".join(t["text"])

    topics = [t for t in topics if len(t["text"].strip()) >= _MIN_SEGMENT_CHARS]

    intro_text = "\n".join(intro_lines).strip()
    segments = []
    if len(intro_text) >= _MIN_SEGMENT_CHARS:
        segments.append({"number": None, "name": "Introduction", "text": intro_text})
    segments.extend(topics)
    return segments


_HEADING_SIZE_TOL = 0.5


def _looks_like_plausible_heading_text(text: str) -> bool:
    """A real heading's title is overwhelmingly letters (plus the odd
    apostrophe/hyphen/comma) — never mostly digits and punctuation.

    Class 11 Chemistry audit (2026-09-03): this book writes concentration
    values immediately followed by an uppercase unit letter with no space
    at all ("0.1M", "0.05N") constantly throughout its Equilibrium
    chapter — far more often than the chapter has real headings — and
    each one satisfies HEADING_RE's zero-space-before-uppercase branch
    (added for a glued heading number elsewhere, e.g. "10.1Introduction")
    exactly the way a real heading does. Worse, `reconstruct_lines`
    sometimes rows several such values from the same equilibrium table
    together into one garbled "line" ("M        0.1M           0    0"),
    which still passes the bare shape/case checks. Filtering by
    alphabetic density catches both: a real title (even this book's own
    letter-spaced ALL-CAPS style, "E Q U I L I B R I U M...") is close to
    100% letters once spaces are ignored, while these are mostly digits.
    """
    non_space = [c for c in text if not c.isspace()]
    if not non_space:
        return False
    alpha_count = sum(1 for c in non_space if c.isalpha())
    return alpha_count / len(non_space) >= 0.5


def _looks_like_headline_fragment(text: str) -> bool:
    """A real heading (or its wrapped second line) reads like a headline:
    either fully ALL-CAPS, or with most of its words capitalized. An
    ordinary sentence only capitalizes its first word, everything else
    stays lowercase.

    Class 11 Chemistry audit (2026-09-03): this book's heading font size
    sits ~0.1pt from its own body text (see clean_topics()'s wrap-merge
    length cap for the same finding) — size can't tell a genuine wrapped
    title line ("...PROCESSES") apart from the first line of the
    paragraph that follows it ("The characteristics of system at
    equilibrium..."), since both clear the size check identically. This
    catches the same distinction a different way: real title fragments
    are headline-capitalized throughout, ordinary prose is not.

    Class 12 Chemistry audit (2026-09-03), reused as a universal
    _is_real_heading gate: this book numbers its in-text worked "Example"
    problems with the same chapter.section scheme as real headings,
    scattered through the body rather than confined to one end-of-chapter
    block a page cutoff could exclude ("1.4 Calculate the mass of urea
    (NH2CONH2) required..."). Minor words (the connectors a real
    multi-word title legitimately keeps lowercase — "Complement OF A
    Set" would be wrong) are excluded from the capitalization count
    instead of counting against it, so a real heading with several of
    them ("Nature of Matter", "Complement of a Set") doesn't fail the
    same test that correctly rejects an actual sentence, where only the
    first word is ever capitalized and everything else — including the
    non-minor content words — stays lowercase.
    """
    words = [w for w in text.split() if any(c.isalpha() for c in w)]
    if not words:
        return False
    if text.isupper():
        return True

    # Class 11 Chemistry audit follow-up (2026-09-03): some of this
    # book's real headings are only sentence-cased, not full headline
    # case ("Thermodynamic terms", "Applications") — the ratio check
    # below correctly rejects these as if they were prose. Worse, some
    # headings mix the two styles WITHIN one title ("Enthalpy change, ∆H
    # of a[n unnamed reaction]" — only the first word capitalized, the
    # rest sentence-case). A genuine sentence (an in-text worked
    # Example's problem statement) always runs considerably longer than
    # any real heading in this corpus (every confirmed example seen is
    # 8+ words; the longest genuine heading seen is ~7), so a short
    # candidate is safe to accept on capitalized-first-word alone rather
    # than requiring every significant word capitalized.
    if len(words) <= 6:
        return words[0][0].isupper()

    def _first_alpha_is_upper(w: str) -> bool:
        for c in w:
            if c.isalpha():
                return c.isupper()
        return False

    significant = [w for w in words if w.lower().strip(".,:;!?()'\"") not in _HEADING_MINOR_WORDS] or words
    cap_count = sum(1 for w in significant if _first_alpha_is_upper(w))
    return cap_count / len(significant) >= 0.8


_PROSE_LEAD_WORDS = {
    "the", "this", "these", "that", "those", "as", "like", "such", "there",
    "here", "it", "its", "we", "you", "they", "he", "she", "when", "if",
    "unless", "because", "however", "then", "so", "also", "thus", "hence",
    "now", "once", "while", "although", "though", "since", "from",
}

# Bare prepositions that complete a noun phrase left dangling by the
# heading's own captured text ("types" + "of reactions") -- unlike
# _PROSE_LEAD_WORDS above, a fresh body sentence essentially never opens
# on one of these with nothing preceding it.
_LEAD_CONNECTOR_WORDS = {"of", "in", "for", "with", "between", "among", "under", "over"}

# Class 11 Chemistry audit follow-up (2026-09-03): "Classical mechanics,
# based on Newton's" opens with a real content word (not a
# _PROSE_LEAD_WORDS stopword) and its lone capitalized proper noun
# ("Newton's") happens to clear the 0.5 capitalization-ratio bar anyway
# ("Quantum Mechanical Model of Atom" + this sentence both read as
# headline-shaped by ratio alone). A finite verb or participle appearing
# ANYWHERE in the fragment is a much more reliable "this is a clause,
# not a noun-phrase title" signal than capitalization, which a stray
# proper noun can fake.
_PROSE_VERB_SIGNAL_WORDS = {
    "based", "used", "called", "known", "shown", "found", "made", "seen",
    "consists", "consist", "depends", "depend", "results", "result",
    "occurs", "occur", "requires", "require", "involves", "involve",
    "shows", "indicates", "indicate", "refers", "refer", "means",
    "describes", "describe", "represents", "represent", "gives", "give",
    "leads", "lead", "follows", "follow", "arises", "arise", "denotes",
    "denote",
}


def _looks_like_title_continuation(text: str, prev_name: str | None = None) -> bool:
    """Stricter than _looks_like_headline_fragment, for wrap-merge
    continuation sites only (appending a second print-line onto a
    heading name that has no body text yet).

    Chemistry 11 audit (2026-09-03): _looks_like_headline_fragment's
    <=6-word shortcut ("just check the first letter is capitalized") was
    designed to accept genuine short sentence-case headings like
    "Thermodynamic terms", but every English sentence also starts with a
    capital letter — so it was just as happy to accept the opening of
    the body paragraph that follows a heading ("The experiment
    corresponding to reaction...", "As already mentioned, alkanes are
    saturated...", "Benzene and polynuclear hydrocarbons...") and merge
    it straight into the heading's name. A continuation merge is a much
    narrower claim than "is this text headline-shaped at all" (used
    elsewhere to validate a whole one-line heading candidate), so it can
    afford to be pickier: reject outright if the line opens with a
    common sentence-starter word (a real title continuation picks up a
    noun phrase — "and Types of Reactions" — never "The"/"As"/"These"),
    and require a materially higher share of its own content words to
    be capitalized rather than accepting on the first word alone.

    Class 11 Chemistry ch8 follow-up (2026-09-03): this book's heading
    font also has a small-caps-as-random-mid-word-capital defect (see
    _has_corrupted_midword_capitals in schema.py) — a genuine single-word
    continuation like "Compounds" can render as "cOmPOunds", with no
    uppercase letter at position 0 at all. _looks_like_headline_fragment's
    own <=6-word shortcut checks only the first word's first letter, so
    it rejects exactly these corrupted-but-genuine continuations; this
    function is therefore self-contained rather than gated through it,
    and its capitalization count looks for an uppercase letter ANYWHERE
    in each word, not just the first.

    Class 11 Chemistry ch5 follow-up (2026-09-03): "5.7 Gibbs energy
    change and" continuing with "equilibrium" is genuine (the real title
    is "Gibbs energy change and equilibrium"), but an all-lowercase
    single word can't clear the capitalization ratio on its own —
    exactly the same ambiguity that makes short sentence-case headings
    ("Thermodynamic terms") indistinguishable from prose by
    capitalization alone. The heading-so-far ending in a bare
    coordinating word ("and", "of", ...) is a strong independent signal
    that the title was cut off mid-phrase and more of it is coming, so a
    short, otherwise-unobjectionable candidate gets the benefit of the
    doubt when the caller passes what's been accumulated so far.
    """
    words = [w for w in text.split() if any(c.isalpha() for c in w)]
    if not words:
        return False
    first_clean = words[0].strip(".,:;!?()'\"").lower()
    if first_clean in _PROSE_LEAD_WORDS:
        return False
    if any(w.strip(".,:;!?()'\"").lower() in _PROSE_VERB_SIGNAL_WORDS for w in words):
        return False
    if text.isupper():
        return True

    if prev_name is not None:
        prev_words = prev_name.strip().split(" ")
        prev_last = prev_words[-1].strip(".,:;!?()'\"").lower() if prev_words else ""
        if prev_last in _HEADING_MINOR_WORDS and len(words) <= 2:
            return True

    # Class 11 Chemistry ch5 follow-up (2026-09-03): "5.5 Enthalpies for
    # different types" continuing with "of reactions" is the same
    # incomplete-noun-phrase pattern as above, but the heading-so-far
    # ends in a real content word ("types"), not a connector -- the
    # connector is on the CONTINUATION's side instead. A short fragment
    # that opens with a bare preposition ("of", "in", "for"...) is
    # exactly how a title continuation completes a noun phrase, and
    # essentially never how a fresh body sentence starts.
    if len(words) <= 3 and first_clean in _LEAD_CONNECTOR_WORDS:
        return True

    significant = [w for w in words if w.lower().strip(".,:;!?()'\"") not in _HEADING_MINOR_WORDS] or words
    cap_count = sum(1 for w in significant if any(c.isupper() for c in w))
    return cap_count / len(significant) >= 0.5


def _numbered_heading_calibration(pdf_path: Path, lines: tuple | None = None) -> tuple[float, str | None]:
    """Calibrate the real "N.M Heading" font size for this specific chapter
    rather than trusting the shared MIN_HEADING_SIZE floor on its own, AND
    the chapter's own leading number (the "N" every real heading shares).

    Found auditing Science/Maths (Rule 3 follow-up, 2026-09-02): decimal
    numbers in body prose and tables (temperature readings like "37.0 °C",
    measurement worked examples like "10.4 cm...") coincidentally match
    HEADING_RE's "\\d+\\.\\d+\\s+..." shape and render well above the old
    12.0pt floor, so they were being promoted into bogus topics that
    fragmented the real one. Real chapter headings are reliably the
    LARGEST text matching this shape (confirmed empirically across
    classes 6-10: real headings range from 12pt in class 10 Maths to 17pt
    in class 6, always above any same-shaped false positive in the same
    chapter), so the max observed size — not a single global constant —
    is the per-chapter signal.

    Class 11 Maths audit (2026-09-03): size alone isn't always enough —
    chapter 9 had a garbled equation fragment ("1.9 + 3. �3 and y==
    0. 1+ 3") that happened to render AT the calibrated heading size,
    shape-matching as if it were section "1.9" of the chapter. Every NCERT
    book in this corpus numbers its sections "<chapter number>.<section>"
    — a real heading's leading number is always the chapter's own, and
    that number recurs across every real heading in the chapter, whereas
    a stray same-sized false positive is a one-off. The mode of the
    leading number among heading-sized matches is therefore a reliable
    second signal, cheap to compute alongside the size and needing no
    per-book calibration of its own.

    `lines` lets a caller pass an already-sliced page range (extract_
    exercises.clean_topics() calibrates only up to its chapter's back-of-
    book solutions appendix, not the appendix text itself) instead of
    always calibrating against the whole document.

    Class 11 Physics audit (2026-09-03): this book's real headings render
    at ~10pt — below MIN_HEADING_SIZE (12.0), which classes 6-10's Maths/
    Science books never went under. Gating candidates on that floor found
    ZERO of this book's real headings, so calibration silently fell back
    to (MIN_HEADING_SIZE, None) — and with dominant_number None, the
    chapter-number gate in _is_real_heading no-ops, letting through every
    monotonically-increasing decimal fragment in the chapter's body prose
    unchecked. MIN_HEADING_SIZE was only ever a stand-in for "not
    obviously subscript/superscript noise", not a real per-book
    assumption, so the floor here is dropped to a much lower sanity
    minimum instead — comfortably below every book's real heading size
    seen so far, comfortably above the ~5-8pt subscripts/superscripts
    found scattered through worked examples.
    """
    _CALIBRATION_SIZE_FLOOR = 8.0
    candidates = []
    for page_lines in lines if lines is not None else all_pages_lines(str(pdf_path)):
        for line in page_lines:
            m = HEADING_RE.match(line["text"])
            if (
                m
                and line["size"] >= _CALIBRATION_SIZE_FLOOR
                and _looks_like_plausible_heading_text(m.group(3))
                and _looks_like_headline_fragment(m.group(3))
            ):
                candidates.append((line["size"], m.group(1)))
    if not candidates:
        return MIN_HEADING_SIZE, None
    heading_size = max(size for size, _ in candidates)
    numbers_at_size = [num for size, num in candidates if abs(size - heading_size) <= _HEADING_SIZE_TOL]
    dominant_number = Counter(numbers_at_size).most_common(1)[0][0] if numbers_at_size else None
    return heading_size, dominant_number


def _numbered_heading_size(pdf_path: Path) -> float:
    return _numbered_heading_calibration(pdf_path)[0]


def _is_real_heading(m: re.Match, size: float, heading_size: float, dominant_number: str | None, last_minor: float | None) -> bool:
    """Accept a HEADING_RE match as a real topic heading, not just a
    same-shaped false positive.

    Class 11 Maths audit (2026-09-03): some genuine headings in this book
    render a full 1pt off the chapter's own calibrated size — not just the
    chapter's last heading, but scattered through it ("12.4 Limits of
    Trigonometric Functions" and "14.1 Event" both print at 11pt against
    this book's calibrated 12pt). The strict +/-0.5pt tolerance alone
    drops these, and every later heading in the chapter along with them
    (nothing restarts `current`). A real heading's minor number always
    increases in printed order, though; a same-shaped false positive from
    body prose (a decimal measurement, a cross-reference to another
    section) has no reason to also continue the sequence forward, so an
    out-of-tolerance match is accepted only when it BOTH carries this
    chapter's own number and picks up where the last real heading left
    off. No separate size floor applies here — MIN_HEADING_SIZE (12.0pt)
    is itself above the very outlier sizes (11pt) this path exists to
    recover, so re-imposing it here would just reject them a different
    way; the number+sequence combination is the guard instead.

    Found opening up that secondary path (same audit): an exercise-list
    marker ("8." in bold) sitting immediately before a stacked-fraction
    worked example ("7⁄1, 21⁄3, 7⁄...") reconstructed as one glued row —
    the marker's own trailing period and the fraction's leading digit
    read as "8.7", with the fraction's punctuation as its "title". A real
    heading's title is always a Title-Case word, never leading punctuation
    or digits, so this shape alone is enough to reject it regardless of
    which path would otherwise accept it.
    """
    if not m.group(3)[:1].isalpha():
        return False
    if not _looks_like_plausible_heading_text(m.group(3)):
        return False
    if not _looks_like_headline_fragment(m.group(3)):
        return False
    if dominant_number is not None and m.group(1) != dominant_number:
        return False
    if abs(size - heading_size) <= _HEADING_SIZE_TOL:
        return True
    minor = float(m.group(2))
    return last_minor is None or minor > last_minor


def detect_topics(pdf_path: Path) -> list[dict]:
    heading_size, dominant_number = _numbered_heading_calibration(pdf_path)
    topics = []
    intro_lines = []
    current = None
    last_minor = None

    for page_lines in all_pages_lines(str(pdf_path)):
        heading_block = None
        for line in page_lines:
            text, size, block_num = line["text"], line["size"], line["block"]
            if _is_corrupted_body_line(text):
                continue
            m = HEADING_RE.match(text)

            if m and _is_real_heading(m, size, heading_size, dominant_number, last_minor):
                if current:
                    topics.append(current)
                current = {
                    "number": f"{m.group(1)}.{m.group(2)}",
                    "name": _clean_heading_name(m.group(3)),
                    "text": [],
                }
                heading_block = block_num
                last_minor = float(m.group(2))
                continue

            # a heading title that wrapped onto a second line: same large
            # size, doesn't restart the numbering pattern, no body text
            # collected yet, AND (task 5 finding) from the SAME PyMuPDF
            # block as the heading line itself — a genuine 2-line wrap is
            # one block, whereas an unrelated large-font callout (e.g. an
            # "Activity 5.4" box) that happens to follow immediately in
            # reading order is always a separate block.
            # Class 11 Chemistry audit (2026-09-03): a book whose headings
            # sit only ~0.1pt above its own body text size (see
            # extract_exercises.clean_topics()'s identical fix) lets this
            # branch's size check stay satisfied clean through the first
            # paragraph after the heading, absorbing the whole thing into
            # the name one line at a time since nothing here ever starts
            # current["text"]. The headline-capitalization shape check is
            # the real fix; the length cap stays on as a hard backstop.
            if (
                current
                and not current["text"]
                and len(current["name"]) + len(text) < _MAX_HEADING_CHARS
                and abs(size - heading_size) <= _HEADING_SIZE_TOL
                and not m
                and not ANY_NUMBERED_HEADING_RE.match(text)
                and block_num == heading_block
                and _looks_like_title_continuation(text.strip(), prev_name=current["name"])
                and not _looks_like_garbled_fragment(text.strip())
            ):
                current["name"] += " " + _clean_continuation_fragment(text)
                continue

            # Class 11 Chemistry audit follow-up (2026-09-03): a chapter
            # subscript rendered as its own tiny-size span ("Enthalpy
            # change, ∆H of a" / [size-6pt] "r" / "reaction – Reaction
            # Enthalpy", the subscript r in ∆rH) fails the size-tolerance
            # check above and falls through to current["text"].append,
            # which permanently ends the merge window for every real
            # continuation line that follows. A fragment this short, in
            # the heading's own block, before any real body text has
            # started, is never genuine body content -- drop it instead
            # of letting it block the rest of the title.
            if current and not current["text"] and block_num == heading_block and len(text.strip()) <= 3:
                continue

            if current:
                current["text"].append(text)
            else:
                intro_lines.append(text)

    if current:
        topics.append(current)

    for t in topics:
        t["text"] = "\n".join(t["text"])

    segments = []
    if intro_lines:
        segments.append({"number": None, "name": "Introduction", "text": "\n".join(intro_lines)})
    segments.extend(topics)

    if not topics:
        # no "N.M Heading" numbering found anywhere in the chapter — try
        # the ALL-CAPS fallback (task 11 finding) rather than shipping the
        # whole chapter as one undifferentiated chunk.
        fallback = _detect_topics_allcaps_fallback(pdf_path)
        if any(s["number"] is not None for s in fallback):
            return fallback

    return segments


### Revised-curriculum Social Science books (task 15 / SPEC.md section 6,
### mandatory per-textbook rule): a genuinely different heading design from
### both the numbered "N.M Heading" style above and the older ALL-CAPS
### Social Science books _detect_topics_allcaps_fallback was built for.
### Verified across all 14 chapters of class 6 Social Science (2026-09-02):
### this book's heading hierarchy is font-size-tiered, not text-pattern- or
### color-based (color turns out to vary chapter-to-chapter, same finding
### as this book's chapter-TITLE color — see schema.py's tier 0) — real
### topics render at ~17pt, subheadings within a topic at ~15pt, and a
### small fixed set of recurring callout-box labels at ~14pt that are not
### headings at all. Scoped for now to class 6 (explicit opt-in dispatch in
### schema.py) — later classes in this same production era likely share
### this design but haven't been individually verified yet, per the
### "one textbook at a time" process.

# Real headings in this book are always Title Case; the only observed
# ALL-CAPS text at heading-adjacent sizes is decorative noise that isn't a
# heading at all — a map/diagram label stretched across a graphic (e.g.
# "HINDU KUSH", or "HIMALAYAS" spelled one enlarged letter per span). A
# single "has at least one lowercase letter" check cleanly rejects both
# that noise and content-free bullet/symbol artifacts (which have no
# letters at all, hence no lowercase either) while accepting every real
# topic/subheading observed, which are all mixed-case.
#
# A real heading also never runs long — the longest genuine one observed
# across all 14 chapters (a merged 2-line topic title) is 66 characters.
# Found in production: a bulleted list item ("Anekāntavāda means 'not just
# one' aspect or perspective...") happened to print at this book's
# subheading size, and its own bullet-glyph character merged with it into
# a bogus "subheading" — this length cap rejects that ordinary-sentence
# shape outright regardless of size, with comfortable margin above every
# real heading seen.
# Class 11 Maths audit (2026-09-03): "Algebraic Solutions of Linear
# Inequalities in One Variable and their Graphical Representation" is a
# genuine two-line heading whose combined length (95 chars) cleared 90 —
# the continuation-merge sites' own capitalization/prose-lead checks are
# now the real guard against absorbing body prose (see
# _looks_like_title_continuation), so this backstop only needs to rule
# out whole paragraphs, not trim a few genuinely long titles.
_MAX_HEADING_CHARS = 110

# A bare superscript ordinal suffix ("13ᵗʰ to 17ᵗʰ Centuries" — the "th"
# renders as its own small span, same rendering quirk already found and
# fixed for chapter TITLES elsewhere in this pipeline, schema.py's
# _color_banner_runs) — never a heading on its own, but has lowercase
# letters and can land in either size range depending on the chapter, so
# it needs an explicit exclusion rather than relying on the general shape
# checks above. A title with TWO such date-range ordinals ("13th to
# 17th") has both superscripts land at the same row position and merge
# into one reconstructed line reading "thth" (this pipeline's row-based
# duplicate-glyph clustering doing exactly what it's supposed to for a
# same-position repeat, just not a real duplicate here) — found in
# production producing a bogus "thth" topic on page 0 that swallowed what
# should have been the chapter's real Introduction content. Matches one
# or more concatenated copies of any ordinal suffix, not just a lone one.
_BARE_ORDINAL_SUFFIX_RE = re.compile(r"^(st|nd|rd|th){1,4}$", re.IGNORECASE)

# A bare lettered list marker ("a)", "b)"...) or one merged with its own
# title text on the same line ("d) Air transport") — always a sub-level
# list item in every chapter checked, never a real chapter topic, even
# when (found in production, class 7) one sibling marker in an otherwise-
# consistent a/b/c/d/e list happens to render at this book's topic size.
_LETTERED_SUBHEADING_RE = re.compile(r"^[a-hA-H]\)(\s|$)")


def _looks_like_heading_text(text: str) -> bool:
    if _BARE_ORDINAL_SUFFIX_RE.match(text):
        return False
    return len(text) <= _MAX_HEADING_CHARS and any(c.islower() for c in text)


_TOPIC_SIZE_RANGE = (16.5, 17.5)
_SUBHEADING_SIZE_RANGE = (14.5, 15.5)
# A bare page number, a page-footer branding line, or a stray margin-note
# glossary definition ("History: The study of the human past.") shouldn't
# count as real Introduction/topic content on its own — every genuine
# segment observed runs at least to a few hundred characters, comfortably
# clear of this floor. Also used for real topics, not just the
# Introduction: found in production, a heading sitting at the very bottom
# of a page directly above a table/diagram (whose own text gets jumbled
# out of reading order ahead of it — the table-layout limitation already
# documented for `topic_page_ranges`) can end up with nothing after it but
# the next page's running-header/page-number line before the next real
# heading arrives.
_MIN_SEGMENT_CHARS = 80
# The lower bound a discarded activity box's own body text needs to clear
# to signal "the box ended" — callout-box labels themselves print at
# exactly 14.0pt in every chapter checked, so anything at or above that
# is either a new label or a real heading, never the box's ~12-13pt body.
_BOX_LABEL_SIZE_FLOOR = 14.0

# Apostrophe-position-agnostic ("." matches both a straight and curly
# apostrophe, and the one observed misspelling "LET US EXPLORE") — these
# are hands-on activity prompts and reflective/open-ended questions with
# no gradable facts (verified by reading several: "draw a map of your
# school", "what's your earliest memory you can recollect?"), excluded
# from grounding text entirely per the mandatory rule in SPEC.md section 6.
# "LET'S REMEMBER" (found in class 7) is the same category — verified by
# reading several: a prior-grade recall prompt ("Recall that in Grade 6,
# we saw the meaning of the word 'constitution'...") or a group-discussion
# activity, never new factual content specific to the current chapter.
_ACTIVITY_BOX_RE = re.compile(r"^(LET.?S EXPLORE|LET US EXPLORE|LET.?S REMEMBER|THINK ABOUT IT)$", re.IGNORECASE)
# Unlike the activity boxes above, this book's "DON'T MISS OUT" box carries
# real supplementary facts (verified: 2004 Indian Ocean tsunami details,
# India's disaster-management authority) — only its own label line is
# skipped; the box's body text falls through into the enclosing topic's
# text normally, same as any other paragraph.
_FACT_BOX_RE = re.compile(r"^DON.?T MISS OUT$", re.IGNORECASE)
# End-of-chapter bullet recap restating facts that span several topics in
# one summary — not attributable to any single topic, so it's dropped
# rather than misattributed to whichever topic happens to be last.
_RECAP_RE = re.compile(r"^Before we move on\s*[.…]*$", re.IGNORECASE)
# This book's exercises section (SPEC.md section 6): captured separately
# as reference_corpus rows with no answer (a question-wording style
# reference for generation), not part of any topic's grounding text. The
# exact label varies by class — class 6 prints "Questions, activities and
# projects" (comma before "activities", "and" before "projects"), class 7
# drops "projects" entirely AND the comma ("Questions and activities") —
# matched as two explicit alternatives rather than guessing a single
# pattern flexible enough for both, since the connecting word changes
# (comma vs. "and") depending on which item is dropped.
_EXERCISES_HEADER_RE = re.compile(r"^Questions,\s*activities\s*and\s*projects$|^Questions\s*and\s*activities$", re.IGNORECASE)
# A new numbered exercise item ("1.\tExplain the following terms:",
# "2. \t Let us draw – ...") — the same shape extract_exercises.py's
# BOX_QUESTION_START_RE uses for the Maths book's in-text boxes, redefined
# here rather than imported to avoid a circular import (extract_exercises
# already imports from this module).
_EXERCISE_ITEM_START_RE = re.compile(r"^\d+\.[\s\t]+")


def _group_exercise_lines(lines: list[str]) -> list[str]:
    """Merge this chapter's raw exercise-section lines into one string per
    numbered question (a question's own text wraps across several lines,
    same as any other paragraph in this book)."""
    questions: list[str] = []
    current: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if _EXERCISE_ITEM_START_RE.match(stripped):
            if current:
                questions.append(" ".join(current))
            current = [stripped]
        elif current:
            current.append(stripped)
        # a line before the first numbered item (rare) has nothing to
        # attach to and is dropped rather than guessed at
    if current:
        questions.append(" ".join(current))
    return questions


SUBHEADING_MARKER = "\x00SUBHEADING\x00"


def _block_min_size(page_lines: list[dict], block_num: int) -> float:
    """The smallest font size among this block's own lines — used to tell
    a genuine heading block (every line in it is heading-sized) apart from
    an activity box's own instruction line printed large for emphasis but
    followed, in the SAME block, by regular-size continuation sentences
    (found in production: a "LET'S EXPLORE" box's prompt, "Put a [checkbox]
    against those activities/professions that you think", prints at 17pt —
    squarely in this book's topic-heading size range — with its next two
    sentences in the same block at a normal 12pt). A single large line in
    isolation can't be told apart from a real heading by size alone; the
    rest of its own block can."""
    sizes = [l["size"] for l in page_lines if l["block"] == block_num and l["text"].strip()]
    return min(sizes) if sizes else 0.0


def detect_topics_ss_revised_curriculum(pdf_path: Path) -> tuple[list[dict], list[str]]:
    """Topic/subheading/exercises detector for the revised-curriculum
    Social Science book design (see module comment above). Returns
    (segments, exercise_question_lines) — segments in the same
    {"number", "name", "text"} shape detect_topics() produces (a
    subheading is embedded as a SUBHEADING_MARKER-prefixed line within its
    parent topic's text, not a segment of its own — chunk_topics.py splits
    on that marker, chaining the resulting chunks back under the SAME
    topic per SPEC.md's mandatory topic/subtopic rule); exercise_question_lines
    is this chapter's "Questions, activities and projects" text, one
    logical question per list entry, for the caller to turn into
    reference_corpus rows.
    """
    topics: list[dict] = []
    intro_lines: list[str] = []
    current: dict | None = None
    collecting_topics = True
    collecting_exercises = False
    discard_box = False
    # A heading (topic or subheading) can wrap onto a second line at the
    # same size, in the same PyMuPDF block, before any body text follows —
    # accumulated here and flushed (into a new topic, or a subheading
    # marker line) as soon as a non-continuation line arrives.
    pending: dict | None = None
    exercise_lines: list[str] = []

    def flush_pending():
        nonlocal current, pending
        if pending is None:
            return
        name = " ".join(pending["parts"])
        if pending["tier"] == "topic":
            if current:
                topics.append(current)
            current = {"number": str(len(topics) + 1), "name": name, "text": []}
        else:
            marked = SUBHEADING_MARKER + name
            if current:
                current["text"].append(marked)
            else:
                intro_lines.append(marked)
        pending = None

    for page_num, page_lines in enumerate(all_pages_lines(str(pdf_path))):
        for line in page_lines:
            text, size, block_num = line["text"], line["size"], line["block"]
            if _is_corrupted_body_line(text):
                continue
            stripped = text.strip()
            if not stripped:
                continue

            if collecting_exercises:
                exercise_lines.append(text)
                continue

            if not collecting_topics:
                # Past the recap but not yet at the exercises header (or
                # this book design has no exercises section at all) — the
                # exercises-header check below must still run on every one
                # of these lines so the transition into collecting_exercises
                # can happen; only a plain "keep dropping this line" falls
                # through when it isn't a match.
                if _EXERCISES_HEADER_RE.match(stripped):
                    collecting_exercises = True
                    continue
                continue

            if discard_box:
                if size >= _BOX_LABEL_SIZE_FLOOR and _block_min_size(page_lines, block_num) >= _BOX_LABEL_SIZE_FLOOR:
                    discard_box = False  # this whole block is heading-sized -> box genuinely ended, reprocess normally
                else:
                    continue

            if _RECAP_RE.match(stripped):
                flush_pending()
                collecting_topics = False
                continue

            if _EXERCISES_HEADER_RE.match(stripped):
                flush_pending()
                collecting_topics = False
                collecting_exercises = True
                continue

            if _ACTIVITY_BOX_RE.match(stripped):
                flush_pending()
                discard_box = True
                continue

            if _FACT_BOX_RE.match(stripped):
                flush_pending()
                continue

            in_topic_range = _TOPIC_SIZE_RANGE[0] <= size <= _TOPIC_SIZE_RANGE[1]
            in_subheading_range = _SUBHEADING_SIZE_RANGE[0] <= size <= _SUBHEADING_SIZE_RANGE[1]
            is_heading_candidate = (in_topic_range or in_subheading_range) and _looks_like_heading_text(stripped)

            if page_num == 0 and not is_heading_candidate:
                # Page 0 is never this chapter's real content once you take
                # out its heading (if any — found in production: usually
                # none at all, occasionally the chapter's own first topic,
                # e.g. "Family" in one chapter, sitting right on page 0).
                # Everything else observed there is decorative noise with
                # no legitimate claim to being "Introduction" text: the
                # chapter title itself, "CHAPTER"/a page number/the "Big
                # Questions" labels (single literal-text exclusions,
                # subsumed by this blanket rule), a letter-by-letter
                # vertical "CHAPTER" spelled as individual single-character
                # lines, an opening literary epigraph/quote with its own
                # attribution line, and the "Big Questions" feature box's
                # own reflective pre-chapter prompts (non-factual, same
                # reasoning as excluding "THINK ABOUT IT" elsewhere) — none
                # of it belongs in this chapter's grounding text. Dropping
                # everything non-heading on page 0 unconditionally is
                # simpler and more robust than pattern-matching each of
                # these different kinds of noise individually.
                flush_pending()
                continue

            if is_heading_candidate:
                # Same block as an already-open pending heading — a real
                # multi-line heading wrap never mixes tiers mid-wrap, so
                # once a block has started a pending group, every further
                # heading-range line in that SAME block extends it,
                # regardless of what its own size alone would say. Found in
                # production why this has to override tier-matching, not
                # just fall out of it naturally: a lettered subheading
                # marker ("b)") and its own title text ("Indian railway
                # network") both rendered at this book's TOPIC size in one
                # chapter — sibling markers "a)", "c)", "d)", "e)" in the
                # same chapter all correctly render at subheading size, so
                # this is a one-off source inconsistency, not a real
                # promotion to topic level.
                if pending and pending["block"] == block_num:
                    pending["parts"].append(stripped)
                    continue
                tier = "topic" if in_topic_range else "subheading"
                # A bare lettered list marker ("a)", "b)"...), alone or
                # immediately followed by its own text on the same line
                # ("d) Air transport"), is never a real top-level topic in
                # any chapter checked — force subheading tier regardless of
                # its measured size, since this is exactly the shape that
                # can render at the wrong tier's size (see above).
                if _LETTERED_SUBHEADING_RE.match(stripped):
                    tier = "subheading"
                flush_pending()
                pending = {"tier": tier, "block": block_num, "parts": [stripped]}
                continue

            flush_pending()
            if current:
                current["text"].append(text)
            else:
                intro_lines.append(text)

    flush_pending()
    if current:
        topics.append(current)
    for t in topics:
        t["text"] = "\n".join(t["text"])

    # A topic heading immediately followed only by an excluded
    # activity/reflection box, with no other content before the next
    # heading, ends up with no real text at all (found in production: a
    # History chapter's topic occupies the rest of its page with nothing
    # but a "THINK ABOUT IT" box), or by only a running page-number/footer
    # line before the next real heading (found in production: a heading
    # sitting at the bottom of a page above a table whose own text reads
    # out of order ahead of it) — either way, a near-empty topic is worse
    # than a missing one: it would show up in the app's topic picker but
    # fail to generate any question at all. Drop it and renumber the
    # survivors so the topic list stays contiguous, matching "position in
    # this chapter's real topic list" rather than leaving numbering gaps.
    topics = [t for t in topics if len(t["text"].strip()) >= _MIN_SEGMENT_CHARS]
    for i, t in enumerate(topics, start=1):
        t["number"] = str(i)

    # A running page number or a margin-note glossary definition
    # ("History: The study of the human past.") can occasionally slip
    # through as the only pre-first-heading content in a chapter that has
    # no real lead-in prose of its own — a bare page number is never
    # legitimate "Introduction" text, and neither is a fragment this short.
    intro_text = "\n".join(intro_lines).strip()
    segments = []
    if len(intro_text) >= _MIN_SEGMENT_CHARS:
        segments.append({"number": None, "name": "Introduction", "text": intro_text})
    segments.extend(topics)

    return segments, _group_exercise_lines(exercise_lines)


### NCERT Physics-style books (Class 11 Physics audit, 2026-09-03): a
### third distinct heading design, different from both the plain "N.M
### Heading" Maths/Science convention and the ALL-CAPS-no-numbers Social
### Science convention. This book numbers its sections "N.M HEADING" like
### Maths/Science, but real section headings render in full ALL-CAPS
### ("2.2  INSTANTANEOUS VELOCITY AND SPEED") — and, uniquely, every
### chapter also prints a "mini table of contents" sidebar on its own
### opening page that RESTATES every real heading with the same "N.M"
### numbers but in Title Case ("2.2 Instantaneous velocity and speed"), a
### pure duplicate that would otherwise double every topic. This book also
### numbers its end-of-chapter EXERCISES questions as a continuation of
### the same per-chapter decimal scheme (chapter 2's real sections run
### 2.1-2.5, then exercise questions continue 2.6, 2.7, ... instead of
### restarting at 1 the way Maths' exercises do) — indistinguishable from
### a real heading by number/shape/sequence alone, since exercise
### questions often start with an uppercase word too ("2.5 A car
### moving..."). Case is the one signal that cleanly separates all three:
### a real heading is fully upper-case, the sidebar duplicate is Title
### Case, and an exercise question is ordinary sentence case (verified by
### reading — no real heading in this book is ever partially-cased).
_PHYSICS_STYLE_HEADING_RE = re.compile(r"^(\d+)\.\s*(\d+)\.?(?:\s+|(?=[A-Z]))(.{2,100})$")


### Class 11 Chemistry chapter 8 ("Organic Chemistry — Some Basic
### Principles and Techniques") audit (2026-09-03): this ONE chapter's
### small-caps rendering glitch is severe enough to lowercase the very
### FIRST letter of a glued heading too ("8.2tetraValence OF carBOn:",
### "8.8methOds OF PuriFicatiOn OF"), not just mid-word letters elsewhere
### (already handled generically by _has_corrupted_midword_capitals in
### _clean_heading_name). No shape/case rule can accept these without
### also accepting decimal-unit contamination elsewhere in the corpus
### that looks identical by case alone — broadening the shared HEADING_RE
### to allow a lowercase letter after a glued number was tried and
### reverted (PARSING_EXCEPTIONS.md section 2g): Chemistry's own
### quantitative-analysis chapters are full of measurements immediately
### followed by a lowercase unit ("41.9 mLof Nitrogen..."), which
### swamped the per-chapter calibration once that door was open. Scoped
### narrowly to just this chapter instead (same "one textbook at a time"
### reasoning as the Class 10 Social Science sub-book detectors): a
### relaxed regex accepting any letter after the glued number, gated by
### a STRICT monotonic-only acceptance (no same-size shortcut at all,
### unlike the shared _is_real_heading) — found in production that this
### chapter's real headings and its own body-prose contamination render
### at the identical font size, so size can't help distinguish them here
### either; only "the chapter's own number, and always a HIGHER minor
### than the last real heading" does. Verified this recovers exactly the
### 10 real headings (8.1-8.10) and rejects the one exercise-fragment
### false positive ("8.2 Differ from each other...", appearing on page 7
### AFTER 8.4/8.5 have already been accepted from pages 5-6 — its minor
### number 2 is not greater than the 5 already seen, so it's rejected on
### sequence alone).
_CHEM11_CH8_HEADING_RE = re.compile(r"^(\d+)\.\s*(\d+)\.?(?:\s+|(?=[A-Za-z]))(.{2,100})$")


def detect_topics_chem11_ch8(pdf_path: Path, end_page: int | None = None) -> list[dict]:
    all_lines = all_pages_lines(str(pdf_path))
    scanned = all_lines[0:end_page] if end_page is not None else all_lines

    topics: list[dict] = []
    intro_lines: list[str] = []
    current: dict | None = None
    last_minor: float | None = None

    for page_lines in scanned:
        heading_block = None
        for line in page_lines:
            text = line["text"]
            stripped = text.strip()
            block_num = line["block"]
            if _is_corrupted_body_line(text):
                continue
            m = _CHEM11_CH8_HEADING_RE.match(stripped)
            is_real_heading = (
                bool(m)
                and m.group(1) == "8"
                and _looks_like_plausible_heading_text(m.group(3))
                and not _looks_like_garbled_text(m.group(3))
                and (last_minor is None or float(m.group(2)) > last_minor)
            )

            if is_real_heading:
                if current:
                    topics.append(current)
                current = {"number": f"{m.group(1)}.{m.group(2)}", "name": _clean_heading_name(m.group(3)), "text": []}
                heading_block = block_num
                last_minor = float(m.group(2))
                continue

            if (
                current
                and not current["text"]
                and block_num == heading_block
                and _looks_like_title_continuation(stripped, prev_name=current["name"])
                and not _looks_like_garbled_fragment(stripped)
            ):
                current["name"] += " " + _clean_continuation_fragment(stripped)
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
    topics = [t for t in topics if len(t["text"].strip()) >= _MIN_SEGMENT_CHARS]

    intro_text = "\n".join(intro_lines).strip()
    segments = []
    if len(intro_text) >= _MIN_SEGMENT_CHARS:
        segments.append({"number": None, "name": "Introduction", "text": intro_text})
    segments.extend(topics)
    return segments


def _is_headline_case(text: str) -> bool:
    """Every word capitalized ("The Simple Pendulum") — found in
    production, Class 11 Physics chapter 13's own last heading prints
    this way instead of this book's usual ALL-CAPS, evidently a one-off
    production inconsistency (every other heading in the same chapter,
    and every heading in every other chapter checked, is plain ALL-CAPS).
    Distinct from the sidebar mini-TOC's Title Case, which capitalizes
    only the phrase's first letter ("The simple pendulum") — requiring
    EVERY word capitalized here still rejects that duplicate.
    """
    words = text.split()
    if len(words) < 2:
        return False
    return all(not w[0].isalpha() or w[0].isupper() for w in words)


def detect_topics_physics_allcaps_numbered(
    pdf_path: Path, end_page: int | None = None, allow_headline_case_fallback: bool = True
) -> list[dict]:
    all_lines = all_pages_lines(str(pdf_path))
    scanned = all_lines[0:end_page] if end_page is not None else all_lines

    numbers = [
        m.group(1)
        for page_lines in scanned
        for line in page_lines
        if (m := _PHYSICS_STYLE_HEADING_RE.match(line["text"].strip())) and m.group(3).isupper()
    ]
    dominant_number = Counter(numbers).most_common(1)[0][0] if numbers else None

    topics: list[dict] = []
    intro_lines: list[str] = []
    current: dict | None = None
    last_minor: float | None = None

    for page_lines in scanned:
        heading_block = None
        for line in page_lines:
            text = line["text"]
            stripped = text.strip()
            block_num = line["block"]
            if _is_corrupted_body_line(text):
                continue
            m = _PHYSICS_STYLE_HEADING_RE.match(stripped)
            right_number = bool(m) and (dominant_number is None or m.group(1) == dominant_number)
            # The headline-case fallback is a weaker signal than ALL-CAPS
            # (it can't tell a real heading from the sidebar mini-TOC by
            # case alone as cleanly), so it only fires for a match that
            # also continues the section sequence forward — never a
            # decorative one-off elsewhere in the chapter's body prose.
            # Class 11 Biology audit (2026-09-03): even with that guard,
            # this fallback is too permissive for a book whose sidebar
            # mini-TOC duplicates are themselves short, plain 2-word
            # Title Case phrases ("Kingdom Monera") — every one of
            # `_is_headline_case`'s own requirements. Physics needed the
            # fallback for one real, confirmed one-off heading
            # ("The Simple Pendulum"); Biology's real headings were
            # verified to be reliably ALL-CAPS with no such exception, so
            # the fallback is opt-in per book rather than always on.
            is_real_heading = right_number and (
                m.group(3).isupper()
                or (
                    allow_headline_case_fallback
                    and _is_headline_case(m.group(3))
                    and (last_minor is None or float(m.group(2)) > last_minor)
                )
            )

            if is_real_heading:
                if current:
                    topics.append(current)
                current = {"number": f"{m.group(1)}.{m.group(2)}", "name": _clean_heading_name(m.group(3)), "text": []}
                heading_block = block_num
                last_minor = float(m.group(2))
                continue

            # A heading title that wrapped onto a second line renders in
            # the same all-caps case and stays in the same PyMuPDF block —
            # found in production, "2.2  INSTANTANEOUS VELOCITY AND" /
            # "SPEED" reconstruct as two lines of the same block.
            if current and not current["text"] and block_num == heading_block and stripped.isupper():
                current["name"] += " " + stripped
                continue

            if current:
                current["text"].append(text)
            else:
                intro_lines.append(text)

    if current:
        topics.append(current)
    for t in topics:
        t["text"] = "\n".join(t["text"])
    topics = [t for t in topics if len(t["text"].strip()) >= _MIN_SEGMENT_CHARS]

    intro_text = "\n".join(intro_lines).strip()
    segments = []
    if len(intro_text) >= _MIN_SEGMENT_CHARS:
        segments.append({"number": None, "name": "Introduction", "text": intro_text})
    segments.extend(topics)
    return segments


def topic_page_ranges(pdf_path: Path) -> list[dict]:
    """Return [{"number", "first_page", "last_page"}] for each topic.

    Needed for task 5b: a table region's own text gets jumbled by
    reconstruct_lines' row-based reading order (it doesn't understand grid
    structure), so linking a table to its chunk by text-substring match
    (as extract_images.py does for figure captions) is unreliable. Page
    range is a fully independent, reliable signal instead.
    """
    heading_size, dominant_number = _numbered_heading_calibration(pdf_path)
    ranges: list[dict] = []
    current_number = None
    last_minor = None

    for page_num, page_lines in enumerate(all_pages_lines(str(pdf_path))):
        for line in page_lines:
            m = HEADING_RE.match(line["text"])
            if m and _is_real_heading(m, line["size"], heading_size, dominant_number, last_minor):
                current_number = f"{m.group(1)}.{m.group(2)}"
                last_minor = float(m.group(2))
                # keep scanning (don't break): a page can contain more than
                # one heading, and the range should reflect whichever topic
                # owns this page by its end, not just its first heading.

        if current_number is None:
            continue
        existing = next((r for r in ranges if r["number"] == current_number), None)
        if existing:
            existing["last_page"] = page_num
        else:
            ranges.append({"number": current_number, "first_page": page_num, "last_page": page_num})

    return ranges


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")

    if len(sys.argv) != 2:
        print("Usage: detect_headings.py <path-to-pdf>")
        sys.exit(1)

    pdf_path = Path(sys.argv[1])
    topics = detect_topics(pdf_path)

    print(f"{len(topics)} segments detected:\n")
    for t in topics:
        preview = t["text"].strip().replace("\n", " ")[:80]
        label = t["number"] or "-"
        print(f"  [{label}] {t['name']!r}  ({len(t['text'])} chars)  {preview!r}...")

    OUTPUT_DIR.mkdir(exist_ok=True)
    out_path = OUTPUT_DIR / f"{pdf_path.stem}_topics.json"
    out_path.write_text(json.dumps(topics, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nFull topic text written to {out_path}")


if __name__ == "__main__":
    main()
