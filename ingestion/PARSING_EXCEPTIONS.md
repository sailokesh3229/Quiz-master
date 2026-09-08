# Known parsing exceptions and limitations (Task 12)

Full corpus ingested: **200/200 chapter PDFs**, all 5 classes (6-10) x 3
subjects (Maths, Science, Social Science), **2927 chunks**, **275**
reference-corpus (exercise Q&A) entries stored in Chroma
(`ingestion/chroma_db`, collections `chunks` and `reference_corpus`).

| Class | Maths | Science | Social Science |
|---|---|---|---|
| 6  | 150 | 153 | 91  |
| 7  | 310 | 116 | 215 |
| 8  | 284 | 169 | 199 |
| 9  | 133 | 242 | 136 |
| 10 | 177 | 199 | 353 |

This document tracks every case where parsing needed a fallback, produced
coarser-than-intended chunking, or was deliberately scoped down for time —
per PLAN.md's task 12 instruction, "so this is tracked rather than
silently inconsistent."

## 1. Table extraction was skipped for the full-corpus run

**What:** `has_table` is `False` on every chunk except the small pilot set
ingested with tables on (class 10 Science, chapter 1 specifically).

**Why:** `page.find_tables()` costs ~65s on a single diagram-heavy,
16-page chapter (heavy vector-graphics pages make MuPDF's line-based table
search slow). At that rate the full 200-chapter corpus would take 3+
hours for table detection alone. `batch_ingest.py --no-tables` was used
for the full run to keep it practical; the pilot subject (class 10
Science, task 10) and one sample chapter (task 5b/6) were run with tables
on and verified correct there.

**Follow-up:** re-run `batch_ingest.py <root> --skip-existing` **without**
`--no-tables` to backfill `has_table` and per-chunk structured tables
across the corpus — safe to do incrementally (upsert), just slow. Table
detection quality itself (the compact-bbox + short-cell-length filter
that separates real tables from false-positive multi-column layouts) was
validated in task 5b and isn't in question — this is purely a
runtime/scope tradeoff, not an accuracy gap.

## 2. 18/200 chapters (9%) fell back to a single undifferentiated chunk

**What:** no topic-level heading was detected anywhere in the chapter, so
the whole chapter became one "intro" chunk instead of being split by
topic.

**Affected chapters:**
- Social Science (14 of 18): class 6 ch1, ch8; class 7 ch1, ch14; class 8
  ch6, ch10; class 9 ch1; class 10 ch2, ch7, ch18, ch19, ch20, ch21, ch22
- Science, chapter 1 specifically (4 of 18): class 6, class 8, class 9

**Why:** two distinct root causes, both found and diagnosed in task 11:

  a. **Social Science's real heading convention is unnumbered ALL-CAPS
     titles** ("LAND UTILISATION", "SOIL AS A RESOURCE"), not the "N.M
     Heading" numbering Science/Maths use. A fallback detector
     (`detect_headings._detect_topics_allcaps_fallback`) was added and
     fixed the *majority* of Social Science chapters (compare: only 14 of
     the subject's ~79 chapters remain unsplit). The chapters still stuck
     at 1 chunk have **no distinguishable heading font size at all** —
     body text and would-be headings render at the same ~10.5pt with
     nothing standing out (confirmed by inspecting class 10 Social
     Science chapter 2's full font-size distribution: 290 lines at
     10.5pt, one single 12pt line that's a decorative repeated
     "EXERCISES" banner, nothing else). These likely use bold styling or
     a different structural cue instead of size, which the current
     detector doesn't check.

  b. **Some "chapter 1"s across classes/subjects are short
     orientation/overview chapters** with no real subsections at all
     (this is plausible content, not necessarily a parsing failure —
     wasn't individually verified against the source PDF for each case
     given the corpus-wide scope, flagged here for a manual check).

**Impact:** these chapters are still fully retrievable and gradeable —
they just retrieve at whole-chapter granularity instead of topic
granularity, so a request scoped to one specific topic within one of
these chapters will get the whole chapter's text instead of a tighter
slice.

**Follow-up:** add a bold/font-weight-based fallback heading detector for
case (a)'s remaining stragglers; manually inspect case (b)'s chapters to
confirm they're genuinely unstructured rather than mis-parsed. **Case (b)
confirmed (2026-09-02):** dumped the full font-size distribution for
class 6/8/9 Science chapter 1 — none contains any text at the size tier
their own later chapters use for real headings (17pt/16.7pt/15pt
respectively); these chapters are genuinely single flowing narratives in
the source PDF, not a detection failure.

### 2a. Follow-up (2026-09-02): class 6 Social Science was worse than documented, and topics/subtopics needed their own rules

Task 11's framing above ("18 chapters fell back to one undifferentiated
chunk") turned out to understate class 6 Social Science's actual problem.
User feedback made three rules mandatory for all future topic/subtopic
extraction work (see SPEC.md section 6): only real topics are ever
stored/surfaced (a large topic's own subtopics chunk-and-chain under it,
never become sibling topics); content before the first topic heading is
named "Introduction" literally; and topic/heading detection is verified
and built per textbook, one textbook at a time — starting with class 6
Social Science.

Investigating class 6 specifically found the ALL-CAPS fallback (2's root
cause "a") doesn't just under-split this book — for every one of its 14
chapters, it **silently produces a plausible-looking but wrong topic
list**: this revised-curriculum book's real headings are Title Case, not
ALL-CAPS, so the ALL-CAPS regex matches zero real headings and instead
latches onto "THINK ABOUT IT" (a recurring callout-box label that happens
to be the one ALL-CAPS string without a curly apostrophe breaking the
match) — that fake "topic" then swallows the entire rest of the chapter's
content. Confirmed in the live corpus before this fix: every one of the
14 chapters' topic lists was polluted with "Think About It" and a
bracketed "(intro, before first heading)" placeholder instead of real
topic names.

**This book's actual hierarchy is font-size-tiered** (verified across all
14 chapters): a real topic renders at ~17pt, a subheading within a topic
at ~15pt, and a small fixed set of recurring callout-box labels at ~14pt
that aren't headings at all — color varies chapter-to-chapter (same
finding as this book's chapter-title color, section 3a) so size, not
color, is the stable signal. `detect_headings.detect_topics_ss_revised_curriculum`
implements this, with `chunk_topics.py` extended to chain a large topic's
subheading-bounded chunks back under the SAME topic (an internal
`SUBHEADING_MARKER` boundary, since this book's subheadings have no
consistent numbering scheme to regex-match the way the Maths/Science
books' "1.1.1" sub-subheadings do).

**Per user direction:** "LET'S EXPLORE"/"THINK ABOUT IT" callout boxes
(activity/reflection prompts, no facts) are dropped from grounding
entirely; "DON'T MISS OUT" boxes (real supplementary facts, verified) stay
in the enclosing topic's text; the "Before we move on ..." end-of-chapter
recap (facts spanning multiple topics in one summary) is dropped rather
than misattributed to the last topic; page 0's decorative content
(chapter title, "Big Questions" reflective prompts, and — in one chapter
— the whole book's own front-matter table of contents bleeding into
chapter 1's PDF) is dropped unconditionally except for a real topic
heading landing there (found in one chapter: "Family" starts on page 0
itself, no separate intro).

**"Questions, activities and projects"** (this book's exercises section)
has no printed answer at all — unlike the Maths book's Q+Ans. appendix, it
doesn't serve section 8a's solutions-style-reference purpose. Per user
direction, it's kept anyway as a *question-wording* style reference for
generation (real NCERT-authored, grade-level-calibrated phrasing, useful
for keeping generated questions readable at the right level) —
`reference_corpus.answer` is now nullable, tagged `source =
'ncert_activity'`, and wired into `prompts.py`'s generation templates
(`_style_reference_block`); `app/explanations.py`'s existing
solutions-style-reference query now excludes these (`answer IS NOT NULL`)
since it needs a real Q+A pair.

**A genuinely separate, pre-existing gap found while wiring this up:**
`reference_corpus` in Postgres had **zero rows** — `embed_and_store.py`
only ever wrote this data to Chroma, and nothing in the codebase before
now synced it to Postgres, so `app/explanations.py`'s solutions-style-
reference feature has been silently returning `None` (its own documented
best-effort fallback) since it was built, for every subject, not just
Social Science. Fixed with a new one-time/re-runnable sync script,
`backend/scripts/sync_reference_corpus.py`; a second, smaller pre-existing
gap noted but not fixed (out of scope for this pass): the Maths book's
`reference_corpus` entries only ever carry a `topic_number` (a "Section
N.M" marker), never a real topic name, so `_get_style_reference`'s
same-topic preference can never actually match and silently degrades to
"any row in the chapter" — harmless (still a valid Q+A pair), just not as
targeted as intended.

**Final result, full 14-chapter re-verification (2026-09-02):** 13/14
chapters produce a clean, correct topic list with no subtopic pollution,
no placeholder names, and exercises correctly separated out. One
known residual: chapter 1 — already an atypical file bundling the whole
book's front-matter "Introduction: Why Social Science?" together with the
real "Chapter 1: Locating Places on the Earth" starting on page 6 — has
one garbled topic entry ("Social The Value Science") from a decorative
background-text element on page 0 that coincidentally renders at this
book's topic-heading size; not chased further given it's isolated to this
one already-unusual file. Re-ingested into the live Chroma corpus (91
chunks across 14 chapters, replacing the 91 wrongly-structured ones) and
synced 91 new exercise entries into Postgres alongside the 275 pre-existing
ones. Full backend test suite (139 tests) passes.

Classes 7-9 Social Science are the same production era (confirmed via PDF
producer metadata during the chapter-title work, section 3a) and likely
share this exact design — class 7 confirmed below (2b); 8-9 still
unverified.

### 2b. Follow-up (2026-09-02): class 7 Social Science — same design, two small additions, one real bug found and fixed

Verified against all 20 of class 7's chapters (a larger book than class 6:
4 sub-books' worth of Geography/History/Civics/Economics content bundled
the same way class 10's Social Science is, confirmed by the repeated
chapter_number values 1-8 appearing on several different real chapter
titles in `app.catalog.list_chapters` output — expected, not a bug, same
pattern already documented for class 10). The font-size-tiered design
(section 2a) holds as-is; two small additions were needed for label
variants this book uses that class 6 doesn't:

- **"LET'S REMEMBER"** — a new activity-box label, added to the same
  discard-entirely category as "LET'S EXPLORE"/"THINK ABOUT IT" (verified
  by reading several: a prior-grade recall prompt, "Recall that in Grade
  6, we saw the meaning of the word 'constitution'...", or a
  group-discussion activity — never new factual content for the current
  chapter).
- **"Questions and activities"** — this book's exercises-section header,
  different from class 6's "Questions, activities and projects" (no
  comma, no "projects", "and" joins the two remaining words instead).
  Matched as an explicit second alternative in `_EXERCISES_HEADER_RE`
  rather than one pattern loosened to fit both, since the connecting word
  changes depending on which item is dropped.

**One real bug found, not a book-design difference:** a lettered
subheading marker ("b) Indian railway network", chapter 19) rendered at
this book's TOPIC font size instead of its usual subheading size — its
siblings in the same list ("a)", "c)", "d)", "e)") all correctly render at
subheading size, so this is a one-off source-PDF inconsistency, not a
different convention. Before the fix, "Indian railway network" was
promoted to its own top-level topic — exactly the Rule 1 violation this
whole effort exists to prevent, just from a new root cause (an
inconsistent render, not wrong logic). Fixed two ways: (1) a bare lettered
marker ("a)", "b)"... optionally followed by its own text, "d) Air
transport") now forces subheading tier regardless of its measured size,
since this shape is never a real topic in any chapter checked; (2) once a
heading candidate is already accumulating within one PyMuPDF block, every
further heading-range line in that SAME block now extends it regardless
of what its own size alone would suggest — a real multi-line heading wrap
never mixes tiers mid-wrap, so this is a safe generalization, not just a
narrow patch for this one case.

**Final result, full 20-chapter re-verification (2026-09-02):** all 20
chapters produce a clean, correct topic list — better than class 6's
13/14, no residual at all. Re-ingested into the live Chroma corpus (283
chunks across 20 chapters, replacing 215 wrongly-structured ones) and
synced 164 new exercise entries into Postgres (530 total, up from 366).
Full backend test suite (139 tests) passes.

### 2c. Follow-up (2026-09-02): class 8 Social Science — same design, one more ordinal-suffix variant

Verified against all 15 of class 8's chapters. Same font-size-tiered
design, both prior additions (2b) already sufficient — no new box labels
or exercises-header variants needed. One new bug, though, same underlying
rendering quirk as chapter-title work (section 3a) and 2b's lettered-
marker fix, in yet another shape: a chapter title with a date range
("Cultural Currents: 13ᵗʰ to 17ᵗʰ Centuries") renders its two superscript
"th" suffixes at the SAME row position on page 0, so this pipeline's
row-based glyph clustering — built to dedupe NCERT's fake-bold
duplicate-render trick, where a repeat at the same position really is a
duplicate — merges them into one line reading "thth". At 17.5pt (this
book's topic range) with lowercase letters, that passed every existing
shape check and became a bogus topic that swallowed the entire
Introduction. Fixed by rejecting any bare ordinal suffix ("st"/"nd"/"rd"/
"th"), including one or more concatenated copies, outright as heading
text — the same *root cause* class as the chapter-title fix, but a fresh
instance since topic detection is a separate code path with its own shape
checks, not something the earlier fix could have covered.

**Final result, full 15-chapter re-verification (2026-09-02):** 15/15
chapters produce a clean, correct topic list (one already-accepted minor
duplicate: chapter 6 has both a real pre-heading "Introduction" segment
and the book's own first topic separately titled "Introduction" — same
harmless pattern as class 6 chapter 2, not a bug). Re-ingested into the
live Chroma corpus (206 chunks across 15 chapters, replacing 199 wrongly-
structured ones); reference_corpus resynced (682 rows, up from 530). Full
backend test suite (139 tests) passes.

### 2d. Follow-up (2026-09-02): class 9 Social Science — same design, last book in this production family

Verified against all 9 of class 9's chapters (a smaller book than 6-8). A
differently-designed title page ("Chapter"/digit/"Big Questions" render
with different sizes/merging than classes 6-8's — "Big Questions" is one
combined 18pt string here rather than two separate spans) needed no code
change at all, since every element of it lands safely outside both
heading-size ranges regardless of exactly how it's split into spans. The
callout-box system (LET'S EXPLORE etc.) doesn't appear to be used in this
book at all — none of the 9 chapters have one — so nothing to add there.

One new failure mode, a different flavor of "near-empty topic" than
section 2b's: a heading ("Literary Heritage of Early India") sits at the
very bottom of a page, directly above a table of literary works laid out
in a grid — this pipeline's row-based reading order (built for linear
prose, not 2D grid layout — the same pre-existing limitation already
documented for `topic_page_ranges`' table-linking) reads the table's own
text out of order, attributing all of it to the PRECEDING topic instead.
The heading itself is left with nothing following it but a running-header
page-footer line before the next real topic starts. Fixed by raising the
existing empty-topic filter from "non-empty" to a minimum content length
(80 characters — comfortably below every genuine topic's length observed
across all 4 classes, confirmed by re-running the full regression on
classes 6-8 with no change in any of their outputs) — the same
"an unhelpful topic is worse than a missing one" reasoning as the
original empty-topic filter, just catching a fragment that survives
`.strip()` but still has zero real content.

**Final result, full 9-chapter re-verification (2026-09-02):** 9/9
chapters produce a clean, correct topic list. Re-ingested into the live
Chroma corpus (100 chunks across 9 chapters, replacing 136 wrongly-
structured ones — fewer real topics survive than the old broken count,
since the old count was inflated by "Think About It"/"(intro, before
first heading)" pollution); chapter titles needed no cross-chapter dedup
correction this time (0 chunks patched); reference_corpus resynced (783
rows, up from 682). Full backend test suite (139 tests) passes.

`schema.py`'s `_REVISED_CURRICULUM_SS_CLASSES` allowlist is now
`{"6", "7", "8", "9"}` — all 4 classes sharing this revised-curriculum
Social Science design are verified against the mandatory topic/subtopic
rules (SPEC.md section 6).

### 2e. Follow-up (2026-09-02): class 10 Social Science's topic-level audit — 3 more genuinely different designs found within one class

Auditing class 10 Social Science's TOPIC-level output (as opposed to its
chapter TITLES, already fixed in tasks 13-14) against the mandatory rules
found the same lesson repeated at a smaller scale: this one class bundles
4 sub-books, and 3 of the 4 needed real work.

- **History (ch 13-17):** already uses the numbered "N.M Heading" path
  directly (`detect_topics()` never falls through to the ALL-CAPS
  fallback for it) — clean, no changes needed.
- **Geography (ch 1-7) and Economics (ch 8-12):** share the ALL-CAPS
  fallback (`_detect_topics_allcaps_fallback`), which turned out to have
  never received any of the hardening built for chapter-title extraction
  (section 3a) or the revised-curriculum detector (2a-2d), despite facing
  the exact same failure classes:
  - **No duplicate-render collapsing** — `_collapse_repeated_runs` was
    only ever wired into chapter-title extraction, never topic detection,
    so garbled duplicate-offset fragments ("G   I E55", "S   I E35")
    became their own bogus topics.
  - **No digit-suffix-stripping before counting repeats** — the exact
    Geography chapter-title bug from section 2a, but for topics: a
    running header with a page number glued on with no separator
    ("SECTORS OF THE INDIAN ECONOMY21", "...23", "...25"...) never
    accumulated a repeat count, so every occurrence looked like a unique
    heading and became its own topic.
  - **No shape/structural-label filtering at all** — "Activity 1",
    "Activity 2", "Chapter I", "Chapter 2"..."Chapter 5", "Additional
    Project / Activity", and the Economics sub-book's recurring "Notes
    for the Teacher" feature box (the same label already known to need
    exclusion at the chapter-TITLE level, but topic detection had no
    equivalent check) were all winning outright as topics.
  - **No end-of-chapter recap/exercises boundary** — this design's
    "SUMMING UP" (a whole-chapter recap, same non-attributable-to-one-
    topic reasoning as "Before we move on" elsewhere) and "EXERCISES"
    (fill-in-the-blank and MCQ questions) flowed directly into whatever
    topic was open, with "EXERCISES" alone never even recognized as a
    boundary at all (it has no space, so it always failed the ALL-CAPS
    candidate shape check that every other check in this function relies
    on).
  - **No line-wrap merging** — unlike `detect_topics()`'s existing
    numbered-heading wrap-merge, this fallback never merged a heading
    split across two lines ("INTERLINKING PRODUCTION ACROSS" / "COUNTRIES",
    the latter a single word that fails the ALL-CAPS candidate's own
    space requirement even when treated as a fresh candidate) — ported
    the same "once a heading is pending, further same-block lines extend
    it regardless of their own shape" reasoning used for the class 8
    ordinal-suffix fix.
  - **The chapter's own title bleeding in as a topic fragment** — this
    book's chapter title can render as a 48pt word plus a 30pt
    continuation in a *different* rendering pass than its own decorative
    banner, and the 30pt piece alone (e.g. "AND THE INDIAN ECONOMY",
    completing "GLOBALISATION AND THE INDIAN ECONOMY") is shaped exactly
    like a real topic. Fixed by threading the chapter's own
    already-extracted title through as `exclude_titles`, fuzzy-matched
    (substring either direction, since the fragment is rarely the whole
    title) — `schema.py`'s `build_chunks()` now passes it whenever this
    fallback is used. **Known gap, not fixed:** this only helps when
    `extract_chapter_meta()` gets the title right on a fresh, single-
    chapter extraction — for chapters 9/10/11/12, whose correct title
    only comes from the CROSS-CHAPTER dedup pass (tasks 13-14's
    `fix_chapter_titles.py`, run offline, not inside `build_chunks()`),
    the single-chapter title is itself wrong ("Notes for the Teacher"),
    so the exclusion doesn't fire — chapter 11 still shows a residual
    "And The Indian Economy" as its first topic.
  - Unlike the revised-curriculum detector, page 0/1 could **not** be
    blanket-excluded: several real topics in this design genuinely start
    there ("TYPES OF FARMING", "MONEY AS A MEDIUM OF EXCHANGE") — the
    title-fragment fix above had to be targeted instead.
  - **Two chapters (Geography 2 and 7) still detect zero topics** — their
    entire heading hierarchy, if it exists at all, renders at the same
    size as body text with no ALL-CAPS/color signal either; unlike
    chapters that needed a different signal entirely (see Political
    Science below), these two seem to have no distinguishable signal at
    all in this pipeline's current signal set (size, color, case). Not
    chased further — this is the original task 11 finding, now confirmed
    narrower (2 chapters, not "several") rather than disproven.
  - **Remaining minor residuals, documented not chased:** chapter 3's
    "W S   N  W" (a compass-rose diagram label, same noise class as the
    map-label issue found in classes 6-9, not caught here since it isn't
    ALL-CAPS-excludable the same way); chapter 9's "Division Of Sectors As
    Organised And Unorganised" / "And Private Sectors" splitting into two
    topics because the real heading is split by several PAGES of
    unrelated content in the source PDF, not a same-block wrap — correctly
    left unmerged, since force-merging across unrelated content would be
    architecturally unsound (this is genuinely two separate real
    headings, just both fragments of one longer conceptual heading).
- **Political Science (ch 18-22):** a **third, previously entirely
  undetected design** — real headings here are Title Case, not ALL-CAPS
  at all, so `_detect_topics_allcaps_fallback`'s own matching regex could
  never have found them; every one of these 5 chapters was falling back
  to a single "Introduction" chunk containing the ENTIRE chapter (task
  11's original "no distinguishable heading size" finding was simply
  wrong for this sub-book — the signal exists, just isn't ALL-CAPS).
  Verified font-size-tiered, same spirit as the revised-curriculum
  detector but different absolute values and no callout-box system at
  all: 20pt = real topic, 13pt = subheading (chains under parent, same
  `SUBHEADING_MARKER` mechanism), 18pt = never a topic (the "Overview"
  label marking each chapter's own intro paragraph, plus occasional
  table/chart captions — both safely excluded by size alone, no separate
  literal-text check needed). New dedicated detector,
  `detect_topics_ss_class10_polsci`, dispatched via an explicit chapter-
  number allowlist (`_CLASS10_POLSCI_CHAPTERS`) in `schema.py`, the same
  "verified allowlist, not auto-detected" pattern as the revised-
  curriculum classes. This sub-book has no end-of-chapter recap/exercises
  heading at all — exercise questions begin with a bare numbered list
  directly after the last topic's own content, with no label to detect
  the boundary — accepted as a source of minor tail-end noise in each
  chapter's last topic rather than risking false positives trying to
  detect an unlabeled boundary.

**Final result, full 22-chapter re-verification (2026-09-02):** History
(5/5) and Political Science (5/5) chapters are fully clean. Geography
(5/7, 2 chapters have no detectable heading signal) and Economics (5/5,
each with 0-1 minor residual) are dramatically improved from a
completely-polluted starting state (every Economics chapter's topic list
full of "Activity 1", "Chapter I", "Notes For The Teacher", and garbled
running-header fragments). Re-ingested into the live Chroma corpus (259
chunks across 22 chapters, replacing 353 wrongly-structured ones — a
large drop reflects how much of the old count was pure pollution, not
real topics); chapter titles re-patched (109 chunks, same
`patch_chapter_metadata.py --all` step now routine after any
re-ingestion). Full backend test suite (139 tests) passes. No exercise-
capture/reference_corpus wiring was built for this older design (out of
scope for a topic-structure audit — SPEC.md section 6's wording-style-
reference feature remains specific to the revised-curriculum books where
the user asked for it).

### 2f. Follow-up (2026-09-02): Maths and Science's numbered-heading path — five real bugs, all pre-dating this session's rule

Audited the actual production path for Maths and Science
(`extract_exercises.clean_topics()`, NOT `detect_headings.detect_topics()`
directly — `schema.build_chunks()`'s else-branch calls the former, which
duplicates the latter's accumulation logic plus an appendix cutoff and
"Figure it Out" box stripping) across all 10 class×subject combinations
(6-10 × Maths/Science, 131 chapters). Unlike the Social Science work,
this wasn't a wrong-architecture problem — the numbered "N.M Heading"
detector is the right design for every one of these books — but five
narrower bugs in it had been shipping wrong data into production the
whole time, none previously caught because nothing had compared its
output against the mandatory rules chapter-by-chapter before:

1. **False-positive topic fragmentation.** Decimal numbers in body prose
   and tables (temperature readings like "37.0 °C", worked-example
   measurements like "10.4 cm...") coincidentally match `HEADING_RE`'s
   "`\d+\.\d+\s+...`" shape and render well above the old, single global
   `MIN_HEADING_SIZE = 12.0` floor — found first in class 6 Science ch5/
   ch6/ch7, confirmed systemic once real heading sizes were sampled
   across classes (12pt in class 10 Maths up to 17pt in class 6 — a
   single global floor can never safely separate real headings from
   these false positives everywhere at once). Fixed with
   `detect_headings._numbered_heading_size()`: per-chapter, take the
   MAX size among shape-matching candidates as that chapter's real
   heading size (a real heading is reliably the largest text matching
   the shape), and require every match be within 0.5pt of it — applied
   to `detect_topics()`, `topic_page_ranges()`, and `clean_topics()`
   (the last needed its own copy, scoped to the pre-appendix page range
   it actually scans).
2. **A decorative icon glyph baked into topic names.** Many headings
   (nearly every one in class 8 Science) render a small "activity" icon
   right after the number, encoded in the PDF's own text as a bare
   control character (`\x07`) — not whitespace, so it survived into the
   stored topic name ("`\x07Why Is Cell Considered to Be a Basic Unit of
   Life?`"). Fixed with `_clean_heading_name()`, stripping `[\x00-\x1f]`
   from the captured name.
3. **A real topic's content silently absorbed into its neighbor.** Class
   9 Maths ch2's own heading punctuates inconsistently: "2.4. Linear
   growth and linear decay" has a trailing period the rest of the
   book's headings don't, so it missed `HEADING_RE` entirely and its
   content merged into "2.3 Exploring linear patterns" instead of
   becoming its own topic. Fixed by tolerating an optional trailing
   period (`\.?`) — safe against reopening the door to bug 1's decimal-
   number false positives, since those still have to clear the size
   calibration, not just this shape.
4. **Title-page furniture stored as "Introduction."** A lone stray
   digit, or the chapter number and title repeated as separate small
   page-0 elements, was being labeled "Introduction" even at 1-40
   characters of pure noise (found down to a single stray "7" in class 8
   Maths ch14). Fixed with the same 80-character minimum the revised-
   curriculum Social Science detector already uses for this exact
   purpose — verified a genuine short intro (a 150-character opening
   epigraph/quote, class 10 Maths ch8) still clears it.
5. **A chapter title extracted as "B."** Class 10 Science chapter 7 opens
   its first paragraph with a decorative drop-cap "B" ("Before we
   discuss...") rendered at 30.1pt — indistinguishable in size from the
   real 30pt title two lines above it — which won chapter-title
   detection outright via the color-banner tier
   (`_color_banner_title_and_number` / `_looks_like_title_candidate`).
   `_big_font_title` already excluded single-letter drop-cap fragments
   on this exact reasoning; `_looks_like_title_candidate` (shared by the
   color-banner tier and the running-header tier) had no such guard.
   Fixed by adding the same `len(body) <= 1` rejection there. Cross-
   checked `chapter_titles_final.tsv` (all classes/subjects, generated
   in an earlier session): this "B" was the only 1-2 character title in
   the entire corpus, so the fix is both necessary and, by construction,
   safe — it can only change a result that was already broken.

**Confirmed non-issues (no fix needed):**
- A book's own first real numbered section is sometimes itself titled
  "N.1 Introduction" (the universal older-NCERT chapter-opening
  convention — confirmed present in literally every chapter of class 10
  Maths, and several class 9 Maths chapters). This creates a same-named
  topic alongside the synthetic pre-heading "Introduction" segment
  (itself usually just title-page bleed) — but `app/catalog.py`'s
  `list_topics()` already groups by topic NAME, not number, so the two
  merge into one "Introduction" entry rather than showing as confusing
  duplicates.
- Class 7 and class 8 Maths each bundle two textbook "Parts" under one
  folder, and both parts restart their own in-book chapter numbering at
  1 (file `chapter 9.pdf` extracts as chapter number 1, titled
  "Geometric Twins" — a different, real chapter from file `chapter
  1.pdf`'s number-1 "Large Numbers Around Us"). Confirmed harmless:
  `chapter_number` is documented as internal-only, used solely for sort
  order, never identity — `list_chapters()`/`list_topics()` key
  everything by chapter NAME, so this never merges or drops a chapter,
  it can only leave the sort order less than ideal for these two books.
- Class 8 Maths chapter 2 has two different real topics both numbered
  "2.5" ("Did You Ever Wonder?" and "A Pinch of History") — confirmed
  via raw span dump this is a genuine typo in the source PDF itself
  (every other chapter's recurring "A Pinch of History" feature is
  correctly the LAST section number), not an extraction bug. Left as-is:
  correctly extracting exactly what the book prints is the right
  behavior; silently renumbering the book's own typo would be
  fabricating information not in the source.

**Result:** all 131 chapters across both subjects re-ingested (1866
chunks, down from 1933 — the net drop is pollution removed, e.g. bug 1's
phantom fragments and bug 4's noise intros, offsetting content recovered
by bug 3's fix); `chapter_titles_final.tsv` corrected for the one "B"
title and `patch_chapter_metadata.py --all` re-run (18 chunks patched to
the corrected title); `sync_reference_corpus.py` re-run (783 rows,
unchanged — Maths/Science's appendix Q&A extraction path is a separate,
untouched code path, so this was an unaffected confirmatory refresh, not
a real update). Verified end-to-end via `app.catalog` for chapters
representing all five bugs. Full backend test suite passes (143 tests).

**Follow-up (2026-09-02, same day): a 6th bug, found by the user reporting a wrong class 9 Maths chapter title.** Class 9 Maths chapter 8 extracted as "Think and Reflect" instead of its real title, "Predicting What Comes Next: Exploring Sequences and Progressions" — a chain of two bugs, both in `_looks_like_title_candidate`/`_color_banner_title_and_number` (chapter-TITLE extraction, not topic detection):
- The real title is 66 characters — over `_looks_like_title_candidate`'s 60-char cap — so it was rejected outright, leaving a same-colored recurring "Think and Reflect" feature-box label (which happened to share the title's exact accent color) as the only surviving same-color candidate. Fixed by raising the cap to 80, matching the cap `_big_font_title` already uses for the identical judgment call — cross-checked against `chapter_titles_final.tsv`: two already-correct titles in the corpus run to 61 characters, confirming 60 was simply too conservative from the start.
- Raising that cap immediately surfaced a **second, latent bug**: when two same-colored candidates survive, picking the "unique largest by font size" (the same reasoning already used for cross-color ties) correctly resolves a real-title-vs-small-label ambiguity, but was too eager whenever a title's own first letter renders as an enlarged drop-cap sharing the title's color — e.g. class 10 Maths, where nearly every chapter title (a per-word drop-cap style: "P" + "OLYNOMIALS", "C" + "IRCLES", "S" + "TATISTICS"...) would resolve to the ALL-CAPS remainder alone, missing its first letter(s), the moment the drop-cap letter itself got rejected by the pre-existing single-character guard (bug 5's own fix, from earlier the same day). Root-caused and fixed at the actual source: `_reattach_dropcap_letters()`, a new pass run on the raw same-color candidate spans before any other merging, pairs a single-letter drop-cap with its immediately adjacent (touching in x, overlapping in y — same visual row, sharing a baseline even though the drop-cap's taller box doesn't share a top edge) remainder span and concatenates them back into the real word — solving both the simple single-word case AND, as a bonus the existing code had explicitly given up on (see its own comment), the multi-word/wrapped case ("REAL"+"NUMBERS", "INTRODUCTION"+"TO" on one line plus "TRIGONOMETRY" wrapped onto the next). A drop-cap with no adjacent partner (class 10 Science ch7's "B", genuinely unrelated body-paragraph decoration) is correctly left alone. Also guarded against a drop-cap candidate being smaller than its "partner" (found in production, class 6 Social Science ch6: an unrelated 16pt glyph-cluster fragment spatially happened to sit next to a huge 72pt garbled decorative symbol and would otherwise have wrongly "recombined" with it) — a real drop-cap is always the larger of the pair by definition.

Verified via a full-corpus tier-0 title scan before and after each change (diffed to confirm zero unintended changes elsewhere) plus the full `extract_chapter_meta()` pipeline for every class 10 Maths chapter and the two originally-reported chapters. `chapter_titles_final.tsv` corrected for class 9 Maths ch8; `patch_chapter_metadata.py --all` re-run (14 chunks patched — class 10 Maths chapters needed no patch since their titles were never actually wrong in the live catalog: `chapter_titles_final.tsv` already held the correct cached value for them from an earlier session, and `patch_chapter_metadata.py --all` had silently overridden the transient bug at ingestion time). Full backend test suite passes (143 tests).

### 2g. Follow-up (2026-09-02, same day): three more real bugs in class 10 Social Science's Geography/Economics/History, found by the user reviewing the app directly

The user reviewed the app's actual chapter/topic lists (not just spot
checks against the mandatory rules in the abstract) and found three
distinct, previously-undetected problems, one per sub-book:

**History: subsections were being promoted to sibling topics (a direct
Rule 1 violation).** This sub-book's real design is `N  Title` (bare
number, 18pt) for a real topic and `N.M Title` (12pt) for a subsection
WITHIN that topic — but it had been routed through the generic numbered-
heading path (`clean_topics`), which treats any "N.M" as chapter.section
and promotes every subsection to its own topic. Confirmed across all 5
chapters (6, 4, 4, 6, and 9 real topics respectively). Subtlety: body
text renders at 11.5pt, only 0.5pt below the subheading tier, and a body
sentence stating cloth prices ("54.7 cm of cloth, in Mainz 55.1 cm...")
coincidentally matches the "N.M " shape at that size — the same class of
decimal-number false positive already fixed for Maths/Science, requiring
a deliberately tight (11.8, 12.2) subheading size range rather than a
loose floor. Fixed with a new dedicated detector,
`detect_topics_ss_class10_history()`, following the same font-size-tiered
+ SUBHEADING_MARKER-chaining pattern as the Political Science detector,
but extracting the book's own printed numbers (topics keep their real
"1"-"9", not synthetic sequential ones) rather than auto-assigning them.
Dispatched via an explicit chapter-number allowlist,
`_CLASS10_HISTORY_CHAPTERS = {"13","14","15","16","17"}`, in `schema.py`.

**Geography: the book is laid out in two side-by-side text columns per
page, and PyMuPDF's own block order doesn't reliably read left-then-
right.** Confirmed via raw span dumps across chapters 1, 2, 4, and 7: a
consistent left column (~x60-215) and right column (~x306-534) on every
576pt-wide page — and one sampled page returned the ENTIRE right column
first, then the entire left column, silently feeding every downstream
detector (topic headings, running headers, chapter titles) the page in
the wrong reading order. Row-clustering itself was never the problem
(`reconstruct_lines()` already scopes it per PyMuPDF block, so it never
merged the two columns into one garbled line) — only the order blocks
are read in. Fixed with `_reorder_two_column_page()`: re-groups a page's
lines by block, classifies each block as left- or right-column by its
own average x-position, and emits every left-column block (in original
relative order) before every right-column block — a stable sort, so nothing
else about the ordering changes. Wired into `all_pages_lines()` itself
(gated by `_is_class10_geography_pdf()`, an explicit chapter 1-7
allowlist checked against the full path, since "chapter 1.pdf" exists in
every subject's folder) so every existing function that already calls it
(`clean_topics`, `_detect_topics_allcaps_fallback`,
`find_appendix_start_page`, `topic_page_ranges`) benefits with no changes
of their own — Economics (chapters 8-12, checked in the same pass) uses
a normal single, narrow text column and needed no such fix.

Two more bugs surfaced once reading order was fixed and chapter 3 could
be inspected properly:
- A compass-rose diagram's scattered N/S/E/W direction labels on chapter
  3's title page, row-clustered by their shared y-position into one bogus
  heading-shaped line ("W S   N  W"), winning as a topic. Fixed by
  rejecting any all-caps candidate that's just a sequence of isolated
  single letters (`_ISOLATED_LETTERS_RE`) — a real heading is never
  shaped like this.
- Chapter 3's real title, "Water Resources", is now correctly supplied as
  `exclude_titles` (see the Economics fix below) — but a later, genuinely
  different, real topic ("Multi-Purpose River Projects and Integrated
  Water Resources Management") legitimately contains "water resources"
  as a substring purely because the chapter IS about water resources, and
  was being wrongly dropped as if it were the title bleeding through. See
  the page-restriction fix below, which resolves this the same way it
  resolves the equivalent Economics case.

**Economics: "Development" (and, less severely, other chapters) had
several topics that were actually table captions, plus a chapter-title-
exclusion mechanism reaching too far.** Three distinct issues:
- A `TABLE N.M Caption Text` heading renders at the exact same size as a
  real topic heading. The line itself never passes the ALL-CAPS shape
  check (its "N.M" always has a literal decimal point, outside the
  allowed character class) and correctly falls through as body text on
  its own — but its WRAPPED CONTINUATION line ("CATEGORIES OF PERSONS"
  completing "TABLE 1.1 DEVELOPMENTAL GOALS OF DIFFERENT") has no number
  or period of its own, passes the shape check cleanly, and was winning
  outright as its own bogus topic — this alone accounted for 4 of
  "Development"'s ~12 topics being table-caption fragments ("Categories
  Of Persons", "Of Select States", "Population Of Uttar Pradesh", "For
  2023"). Fixed with a suppression state, `in_table_caption`: once a
  `TABLE\s+\d` line is seen at heading size, every following still-
  heading-sized line is treated as part of the same caption until a
  normal body-sized line signals the caption (and its table) has ended.
- The existing chapter-title-bleed exclusion (`_is_title_fragment`, a
  fuzzy substring match against the chapter's own title) was too eager
  for a short, generic single-word title: chapter 8's title,
  "Development", made two genuinely different, real topics —
  "National Development", "Sustainability of Development" — look like
  title fragments purely because they contain the common word
  "development", and both were silently dropped. Fixed by skipping the
  fuzzy check entirely when the exclude title is a single word; a
  multi-word title (the case this check actually exists for,
  "Globalisation and the Indian Economy") stays unambiguous.
- Chapters 9-12 all share a "Notes for the Teacher" preface page as their
  own single-chapter `extract_chapter_meta()` result (a known, pre-
  existing cross-chapter collision — see 3a/3b), so `exclude_titles`
  never contained the chapter's REAL title, and a fragment of it
  ("AND THE INDIAN ECONOMY" for chapter 11) leaked through as its own
  topic — the "chicken-and-egg" limitation documented as an accepted
  residual in section 2e. Actually fixed this time: `schema.py` now
  checks `chapter_titles_final.tsv` (tasks 13-14's cross-chapter-deduped
  cache) FIRST via `_known_good_title()`, falling back to the fresh
  single-chapter extraction only when the chapter isn't in that cache —
  since these chapters ARE in the cache with their correct titles, the
  exclusion now works as originally intended. (Chunk metadata's own
  `chapter` field is unaffected — still comes from the fresh per-chapter
  extraction, corrected the usual way by `patch_chapter_metadata.py
  --all`.) This is what exposed the Water Resources/Multi-Purpose River
  Projects false-positive above, which is why the exclusion was further
  restricted to page 0/1 only (the chapter's own title-page banner,
  where the bleed phenomenon actually happens) — a real topic occurring
  later that happens to share the title's words is content, not an
  artifact, regardless of whether the title is one word or several.

**Result:** all 22 class 10 Social Science chapters re-ingested. History:
6/4/4/6/9 real topics per chapter, zero subsections promoted. Geography:
reading order corrected on every page; chapters 1, 3, 4, 5 clean;
chapters 2 and 7 remain genuinely headingless (confirmed no
distinguishable signal at all, not a reading-order artifact); chapter 6
(and possibly 4) may still be missing further real subheadings — a
distinct bold+color signal (`flags=6`, a non-body color) was spotted on
a spot check of chapter 6 marking likely section names ("Chemical
Industries", "Aluminium Smelting") that render at body-adjacent sizes,
but the same color+bold combination also marks ordinary emphasis (figure
captions, inline data labels) elsewhere in the same book, so a reliable
detector needs more investigation than this pass had time for — **not
fixed, flagged as a known open item**, not silently assumed complete.
Economics: "Development" now shows all 8 real topics (previously 5 real
+ 4 table-caption fragments); chapters 9-12 all correctly exclude their
real titles from topic detection, recovering "National Development",
"Sustainability of Development", and "Consumer Rights" (a real, later
section that happens to share its chapter's exact title) as genuine
topics they were previously missing entirely. `chapter_titles_final.tsv`
needed no further correction (all titles were already correct from
section 3c). 105 chunks re-patched by `patch_chapter_metadata.py --all`.
Full backend test suite passes (143 tests).

### 2h. Follow-up (2026-09-03, documented retroactively 2026-09-08): classes 11-12 (Senior Secondary) added to the corpus — real per-book fixes were made in-code but never written up here or in PLAN.md/memory at the time

**Why this entry is dated after the fact:** the work below (code changes,
dated comments, and a full ingest of all 8 class-11/12 subject bundles
into the live Chroma corpus) happened on 2026-09-03, immediately after
section 2g closed out classes 6-10. It was never recorded here, in
PLAN.md's task 15 log, or in the standing `[[rag-extraction-rules]]`
memory — found only by reading the code itself (dated docstrings in
`schema.py`/`detect_headings.py`/`extract_exercises.py`) during an
unrelated full-project review, five days later. Recorded now so the
project's own history is accurate, and because Rule 3's per-textbook
verification for these 8 subject bundles has **not** received the full
"one textbook at a time, dump raw spans, walk the user through it" pass
that classes 6-10 got — only the narrower fixes below (each triggered by
a specific bug found while getting the pipeline to run on these books at
all) plus a coarse post-hoc sanity check (below). Rule 3's full audit for
classes 11-12 remains open work, not something to assume complete.

**Maths (numbered-heading path, shared with classes 6-10 — two new bugs):**
- Glued heading numbers with no space before the title
  ("10.1Introduction"), and once a stray space inserted INSIDE the number
  itself ("10. 5Ellipse") — kerning artifacts of this PDF's own
  typesetting, not a new heading convention. `HEADING_RE`'s separators
  were made tolerant (`\s*` between the two numbers; the title boundary
  now accepts either real whitespace or a zero-width transition straight
  into an uppercase letter) rather than trying to fix the source PDF.
- Chapter 9 had a garbled equation fragment ("1.9 + 3. �3 and y==0. 1+
  3") rendering AT the calibrated per-chapter heading size, shape-matching
  as if it were real section "1.9" — fixed by adding a second calibration
  signal alongside size: a real heading's leading number is always the
  chapter's own and recurs across every real heading, so the MODE of the
  leading number among heading-sized candidates gates out a one-off false
  positive that merely happens to match the shape and size
  (`_numbered_heading_calibration`, shared with `extract_exercises.
  clean_topics()` so the two paths can't drift out of sync).

**Chemistry (mostly the shared numbered-heading path; chapter 8 gets its
own detector) — two real bugs, one now a documented, deliberately
unfixed limitation:**
- Same glued-heading-number quirk as Maths, but this book's small-caps
  rendering glitch sometimes ALSO lowercases the heading's first letter
  after the number ("8.2tetraValence", "8.8methOds"). Widening the
  boundary rule to accept any letter (not just uppercase) was tried and
  **reverted**: this book's body prose is full of measurements followed by
  a lowercase unit ("41.9 mLof Nitrogen..."), and those outnumber the real
  glued-lowercase headings badly enough to dominate per-chapter
  calibration and make chapter 8 worse, not better. Left as a known,
  narrower limitation rather than reopening the shared regex to a much
  larger false-positive class corpus-wide.
- Chapters 7, 8, and 9 (Redox Reactions; Organic Chemistry — Some Basic
  Principles; Hydrocarbons) have a genuine font-embedding defect in the
  source PDF itself: the same named font ("Bookman-Light") decodes to
  readable text in some spans on a page and to substituted-alphabet
  garbage in others on the SAME page ("tKH GHYHORSPHQW RI HOHFWURQLF
  WKHRU\\ RI" for "the development of electronic theory of") — consistent
  with two font subsets sharing one font name, one of them with a broken
  CID-to-Unicode mapping. Confirmed via a full corpus scan that no other
  chapter in classes 11-12 shows this signature; chapter 8 is 41%
  corrupted line-for-line (every page), chapters 7 and 9 only 2-4%.
  **Deliberately not reconstructed** — recovering the original text would
  need decoding the specific broken font's internal glyph table, not
  available from extracted text alone, and guessing risks feeding quiz
  generation subtly wrong science, worse than storing less text. Corrupted
  lines are detected by their raw-control-character signature and dropped
  from stored chunk text entirely; a second detector
  (`_looks_like_garbled_text`/`_looks_like_garbled_fragment`, a wordninja
  word-frequency check: real prose keeps most of its ordinary connecting
  words recognizable even full of technical terms, corrupted runs don't)
  catches garbled spans that corrupt only letters with no control
  character to key on. Chapter 8's pervasiveness needed a dedicated
  detector, `detect_topics_chem11_ch8` (dispatched via an explicit
  `pdf_path.stem == "chapter 8"` check in `schema.py`), rather than
  forcing it through the shared path.

**Physics and Biology, classes 11-12 (a new shared design, not seen in
classes 6-10) — real headings are ALL-CAPS "N.M HEADING", not Title Case:**
- Every chapter also prints a Title-Case "mini table of contents" on its
  opening page, restating every real heading — a naive ALL-CAPS-shape
  match alone can't tell a sidebar restatement from the real heading it
  duplicates. Fixed with `detect_topics_physics_allcaps_numbered`: gates
  on the chapter's dominant leading number (same calibration technique as
  Maths above) AND requires the matched text to actually be ALL-CAPS,
  which the sidebar restatement never is.
- This book's real heading font size runs ~10pt — BELOW the
  `MIN_HEADING_SIZE` floor (12.0) that no book in classes 6-10 ever went
  under. Gating on that floor found zero real headings and silently fell
  back to "no calibration", which also disabled the leading-number gate
  above (no dominant number to compare against) — every monotonically-
  increasing decimal fragment in the chapter's body prose then passed
  unchecked. The floor was dropped to a much lower sanity minimum (8.0pt)
  — comfortably below every book's real heading size seen so far,
  comfortably above the ~5-8pt subscripts/superscripts scattered through
  worked examples, since `MIN_HEADING_SIZE` was only ever standing in for
  "not obviously sub/superscript noise", never a real per-book assumption.
- One real, confirmed one-off: Physics chapter 13's last heading renders
  in Title Case ("The Simple Pendulum") instead of this book's usual
  ALL-CAPS. Added an opt-in headline-case fallback (`allow_headline_case_
  fallback`), gated to only fire when the match also continues the
  chapter's heading-number sequence forward — never a decorative one-off
  elsewhere in body prose.
- Biology (all 19 chapters of class 11) verified to share this exact
  design — same sidebar mini-TOC duplication, same ALL-CAPS real headings
  — added to the same detector's class/subject allowlist rather than
  duplicating it. BUT the headline-case fallback built for Physics's one
  known exception proved **too permissive for Biology**: its own sidebar
  mini-TOC duplicates are themselves short 2-word Title Case phrases
  ("Kingdom Monera") that satisfy every one of the fallback's own
  requirements. Left opt-in per book (`allow_headline_case_fallback`
  parameter) — on for Physics, off for Biology — rather than trying to
  find one shape rule that works for both.
- `_ALLCAPS_NUMBERED_CLASSES = {"11", "12"}`,
  `_ALLCAPS_NUMBERED_SUBJECTS = {"physics", "biology"}` in `schema.py`.

**Result (2026-09-08 post-hoc verification, not a substitute for Rule 3's
full audit):** all 8 subject bundles are ingested with full chapter
coverage matching the source PDFs (Biology 19/19 + 13/13, Chemistry 9/9 +
10/10, Maths 14/14 + 13/13, Physics 14/14 + 14/14 for classes 11/12
respectively — 4,722 chunks total in the live corpus), zero "Unknown"
chapter titles, and no topic names matching known pollution/garble
signatures (sub-subheading numbering, control characters, 1-2 character
names, or overlong ALL-CAPS fragments) on a corpus-wide scan. This is a
structural sanity check, **not** the per-chapter raw-span-dump
verification against Rules 1/2/3 that classes 6-10 received — a genuine
next step, not assumed done by this entry.

## 3. Chapter number/title extraction is best-effort

**What:** `chapter_number` is `None` for books whose cover page doesn't
have an isolated large stylized digit (several Social Science titles hit
this — confirmed on class 10 Social Science chapter 1, "Contemporary
India – II"). `chapter` (title) extraction uses a repeated-running-header
heuristic with a fallback to page-0 large-text reconstruction; both are
tested and working (task 6 findings below), but aren't guaranteed
perfect on every book design in the corpus.

**Task 6 bugs found and fixed in this extractor, for reference:**
- A worksheet table's column headers, jumbled by row-based text
  reconstruction, could satisfy the old "repeats >=2x in the first 6
  pages" running-header check — fixed by requiring appearance on a real
  fraction of the *whole* chapter's pages instead.
- A decorative single-letter drop-cap ("C" from a stylized "CHAPTER")
  passed the large-text title filter — fixed by requiring title
  fragments to be >1 character.
- A book-series branding header ("Ganita Prakash | Grade 6") could
  outrank the real chapter-title header by raw page-count — fixed by
  excluding "|"-containing candidates outright.
- Chapter number glued to the start of the title text with no space
  ("1PATTERNS IN") wasn't caught by an isolated-pure-digit check — fixed
  with a leading-digit regex instead.

**Impact:** `chapter` (title) is reliable; `chapter_number` being `None`
only affects internal ordering/coverage-counting (SPEC.md section 10a) for
those specific chapters, not retrieval or grounding correctness — `topic`
(name) and `topic_number` are unaffected and chunks remain fully
retrievable by `chapter` (title) + `class` + `subject`.

### 3a. Follow-up (2026-08-31): full corpus rework — per-textbook-design extraction

A user report ("class 10 Maths and Social Science chapter names are not
there") led to a full corpus audit (all 204 chapter PDFs) that found the
"chapter title is reliable" claim above badly overstated — many chapters
across several books had a wrong, plausible-looking title, not just a
`None`/"Unknown" one, which the original spot-checks (task 6, a handful
of sample chapters) hadn't caught. This grew into a full rework of
`extract_chapter_meta`, requested explicitly as a **per-textbook-design**
effort — the corpus spans at least two genuinely different production
eras (confirmed via PDF producer metadata: classes 6-9 are Adobe
InDesign, "revised curriculum" era; class 10 is an older Acrobat
Distiller/PageMaker/Ghostscript/Bullzip-era pipeline) plus Social
Science's further split into per-discipline sub-books within a class
(History/Geography/Civics/Economics, most visible in class 10's 4-way
split). Rather than 18 independent hand-written extractors (one per
textbook, matching that framing), the fix converged on **one much
stronger shared core signal** that turned out to generalize across every
era tested, organized as a tiered pipeline with a cross-chapter pass on
top — described below — which better serves the same goal (verified
correctness per textbook) with far less code to maintain per book.

**The key discovery — color, not size, is the reliable title signal.**
Inspecting raw span data (`page.get_text("rawdict")`) directly showed
that a professionally laid out chapter title (and often a "Chapter N"
label) consistently renders in a *display color distinct from the page's
own dominant body-text color* — regardless of whether the book uses a
light-on-dark banner or a dark-on-light one, and regardless of
production era: verified on an InDesign-era class 6 Science book *and*
an old Acrobat-era class 10 Maths book ("REAL NUMBERS" is colored there
too, just not white-on-banner). This is now tier 0 in
`extract_chapter_meta`, tried first: compute the page's dominant color by
total character count, treat any differently-colored text over 13pt as a
title candidate, and pick the best one. It's purely additive — falls
through to the pre-existing tiers 1-3 (repeated running-header, page-0
big-font, page-0/1 single-occurrence) untouched whenever it finds
nothing usable.

**Bugs found and fixed while building and hardening tier 0** (each
found by testing against a real chapter in production, several only
surfacing after an earlier fix in the same session):
- A decorative chapter-number digit sharing the title's color could
  visually *underlay/overlap* the title's own bounding box, merging into
  it as "1The Wonderful World of Science" — fixed by treating any bare
  1-2 digit number or the word "Chapter" as a structural token, excluded
  from the merge pool entirely (used only for `chapter_number`, never as
  a merge participant on either side).
- A two-word drop-cap title (each word's first letter enlarged and
  raised, e.g. "R" + "EAL" + " N" + "UMBERS" for "REAL NUMBERS") produces
  4 independently shape-valid fragments that never remerge under any
  y-gap tolerance, since the raised capitals and lowered remainders don't
  line up as rows — fixed by abstaining (falling through to the tiers
  below) whenever a single color yields more than one surviving
  shape-valid fragment, rather than confidently picking the wrong one.
- A wrapped two-line title ("Cell: The Building " / "Block of Life") can
  have a substantially negative vertical gap between the lines, which
  looks identical to the drop-cap overlap case above unless distinguished
  by x-position — fixed by allowing a much larger negative-gap tolerance
  specifically when both lines start at the same left margin (a strong
  same-block-wrap signal), now that the truly bad overlap case (the
  decorative number) is excluded from this pool by the structural-token
  fix above.
- PyMuPDF can split one visual line into multiple span records mid-word
  on a font/style change even with *no* color change ("Exploring Alge" +
  "braic" continuing on the same line, then wrapping to "Identities") —
  the existing vertical-stack merge logic doesn't fire for same-row text,
  so this needed a separate horizontal-adjacency merge condition (nearly
  equal y0, small x-gap between spans).
- When more than one differently-colored valid candidate survives on a
  page (a real title *and* an unrelated recurring section-feature label,
  e.g. "Big Questions" printed on every chapter of one book), picking the
  longest text picked the label over a short real title ("Big Questions"
  read longer than "Democracy") — fixed by preferring the largest font
  size first, since a real title is reliably the single biggest piece of
  accent-colored text on its page while a recurring feature label is a
  smaller, secondary element.
- ALL-CAPS source text converted via Python's `str.title()` capitalizes
  every word including minor connector words, producing "A Peek Beyond
  The Point" instead of the correct "A Peek Beyond the Point" — fixed
  with a dedicated headline-case converter using the same minor-words
  allowlist as the shape filter (extended along the way: "its", "up",
  "their", and several more common ones were missing and caused their
  own real regressions once caught — see below).
- A title ending on a bare connector word ("Includes the", "Business
  or", "Structure of") is essentially always a merge that dropped the
  real final word(s) — added to the shape filter as an outright
  rejection, since a real title never grammatically ends there.
- A rare source-PDF kerning artifact: a ligature glyph (fi/fl/ffi/...)
  followed by a literal space before the rest of the word it belongs to
  ("Reﬂ ections" for "Reflections") — stripped in final normalization.
- A source PDF can embed a literal line-break character inside a
  colon-subtitle title as one logical span ("The Gupta Era: An Age of
  Tireless\nCreativity") — normalized (along with general whitespace
  collapsing) in the final title string before it becomes a broken TSV
  row or a chapter name with a raw newline in the middle.

**Two regressions introduced and caught during this work, both from the
shape filter being too strict, not too loose:**
1. `str.istitle()` requires *every* word capitalized — real headline
   titles that correctly keep minor words lowercase ("A Peek Beyond the
   Point", "Parallel and Intersecting Lines") failed it and fell through
   to "Unknown" (class 7 Maths regressed to 7/15 "Unknown" before this
   was caught).
2. The minor-words allowlist was missing common words ("its", "up",
   "their") that show up in real titles ("Carbon and its Compounds",
   "State and Society up to 1000 CE") — each caused a real title to be
   rejected and a wrong fallback to win instead, until the specific
   missing word was found and added.

The lesson embedded in the process now (see `fix_chapter_titles.py`'s
resumable runner and the repeated full-corpus re-scans in session
history): every fix was verified against the *whole* 204-chapter corpus,
not just the sample that motivated it, specifically because several
fixes' side effects only showed up elsewhere in the corpus.

**Cross-chapter dedup (`fix_chapter_titles.py`), on top of tier 0-3:** a
book whose running header — or, now that tier 0 exists, whose color-coded
recurring feature label — is the *same* across multiple chapters (real in
Social Science: "Contemporary India – II" is a book title covering many
differently-titled chapters; "Big Questions" is a recurring feature
across several different classes' Social Science books) is
indistinguishable from a real per-chapter signal by any single-file
heuristic. Extracts every chapter in a class+subject first, treats a
title winning for 2+ different chapters as book/series-level, and
re-extracts just those chapters with it excluded from tier 0/1's vote.

**Final corpus-wide result (2026-08-31), full 204-chapter scan after all
fixes above, organized by the textbook-design breakdown this was scoped
against:**

| Design group | Chapters | Honest "Unknown" | Manually spot-checked residual wrong titles |
|---|---|---|---|
| Maths (5 textbooks, one per class 6-10) | 55 | 0 | 0 |
| Science (5 textbooks, one per class 6-10) | 63 | 1 | 0 |
| Social Science, classes 6-9 (4 textbooks) | 58 | 17 | ~8 (see below) |
| Social Science, class 10 (4 sub-book split) | 22 | 0 | ~7 (see below) |
| **Total** | **204** | **18** | **~15** |

Verified fully correct and **patched into the live Chroma corpus**
(2026-08-31): all of Maths and Science (118/118 spot-checked correct, 1
honest Unknown), plus the large majority of Social Science.
`patch_chapter_metadata.py --all` applies `fix_chapter_titles.py`'s
output to already-ingested chunks, matched by the reliable `source_file`
field rather than the old (possibly wrong) `chapter`/`chapter_number`
values — only those two fields are touched, embeddings/text untouched.
1654 of 2927 chunks were corrected in this pass. Full backend test suite
(139 tests) still passes after patching; a separate, unrelated
pre-existing flaky test (`test_worst_case_25_question_cold_bank_quiz`,
which makes real LLM calls and doesn't touch the `chapter` field at all)
failed once during verification — real-provider generation variance, not
a regression from this work.

**Remaining known-wrong titles (~15 chapters, all in Social Science),
not chased further — genuinely different root causes each, diminishing
returns from more general heuristics:**
- **Stylized duplicate-offset rendering** (class 10 Social Science,
  chapters 8/10/11/12): a running header/footer rendered as 4-5
  overlapping copies at large offsets (a bolder/shadow visual effect,
  different from NCERT's usual sub-1pt fake-bold trick) partially defeats
  the same-glyph dedup in `reconstruct_lines`, eating the first letter and
  scattered middle characters — "ONEYANDREDIT" for "Money and Credit",
  "NDERSTANDINGCONOMICEVELOPMENT" for "Understanding Economic
  Development".
- **In-text list of example names mistaken for a title** (class 10 Social
  Science chapter 4): "Brazil, 'Masole' in Central Africa, 'Ladang'" is a
  real sentence from the chapter body (regional names for slash-and-burn
  farming), shape-valid and colored, that happened to be the strongest
  candidate on its page.
- **A print-production artifact leaking into the page** (class 7 Social
  Science chapter 10): "Chapter 10.indd 20908-04-2025 12:54:" — a
  filename + print timestamp stamp, apparently rendered as real page
  content in this specific PDF export.
- **Word-glued adjacent unrelated text** (class 7 Social Science chapter
  2): "TropopauseOzone Layer" — two real words from different nearby
  elements concatenated with no space inserted, a class of bug distinct
  from the mid-word span splits fixed above.
- **A bare number that's part of the title text, not a structural
  ornament** (class 8 Social Science chapter 15): "Cultural Currents: 13ᵗʰ
  to 17ᵗʰ Centuries" — the "13" was (correctly, in every other observed
  case) treated as the chapter-number digit and excluded, but here it's a
  century-range ordinal that's genuinely part of the title; came out as
  "Cultural Currents: to 17".
- A handful of short truncated fragments not caught by the
  ends-on-a-connector-word guard ("Jewish" for what's likely a longer
  title, "Working-age" similarly) and section-divider labels in a
  multi-part book collapsing to just "I Section"/"Ii Section" instead of
  the fuller (still not fully correct) label previously shown.

Each of these needs either a bespoke fix scoped to that one book/pattern
or manual curation — not a general heuristic, since each is a genuinely
distinct failure mode from a small, non-repeating set of source PDFs.
`ingestion/output/chapter_titles_final.tsv` has the full current
per-chapter breakdown (204 rows) for whoever picks these up.

### 3b. Follow-up (2026-09-01): per-sub-book fixes for the Social Science residual

User feedback on 3a's "~15 residual wrong titles, not chased further":
*"there are 4 different textbooks for social science class 10 we need 4
different architecture schemes for chapter name/topic extraction... these
textbooks have different designs"* — correctly pushing back on treating
the residual as unrelated one-offs. Re-investigated each remaining wrong
title by which specific sub-book/textbook it belonged to; nearly all of
them turned out to share a small number of *real, book-specific* root
causes (not one shared bug), each fixed with a targeted change scoped to
that pattern rather than a broad heuristic:

- **Geography sub-book (class 10 SS, chapters 1-7):** every chapter's real
  running-header title never accumulated a repeat count because each
  occurrence had a different page number glued directly onto its trailing
  edge with no separator ("AGRICULTURE31", "AGRICULTURE33", ...), making
  every occurrence look like a unique string. Fixed with digit-suffix
  stripping (`re.sub(r"\d+$", "", t)`) before grouping running-header
  candidates, plus lowering the repeat-count floor from `max(3, pages//4)`
  to `max(2, pages//4)` (a short chapter can have as few as 2 even/odd
  header slots). Now 7/7 correct.
- **History sub-book (class 10 SS, chapters 13-17):** a bare "SECTION"
  label (optionally with a roman-numeral prefix, "II SECTION") is a
  structural divider in this multi-part book, not a title, but won
  outright via the color-banner tier ahead of the real descriptive title
  elsewhere on the page — fixed by rejecting a bare `(?:[ivxlcdm]+\s+)?
  SECTION\.?` pattern in the shape filter, plus stripping the same prefix
  in final normalization for cases where it still merges onto the front
  of the real title. Now 5/5 correct.
- **Economics sub-book (class 10 SS, chapters 8-12), several distinct
  bugs stacked on one badly-behaved book:**
  - `_collapse_repeated_runs`'s regex matched a single repeated
    *character* as well as a repeated multi-char unit, corrupting "up to
    1000 CE" into "up to 10 CE" elsewhere in the corpus — tightened to
    require a 2+ character repeated unit.
  - The cross-chapter dedup's exclusion check was exact-string even after
    being handed multiple casing variants of the same recurring label
    ("Notes for the Teacher" vs "Notes For The Teacher", rendered
    differently by different tiers on the same page) — fixed by
    lowercasing both sides of every exclusion check inside
    `extract_chapter_meta`.
  - The page-0 big-font fallback (tier 2) never checked `exclude_titles`
    at all — a real, previously-invisible gap, since ALL-CAPS candidates
    had never won via this tier before other fixes changed the routing.
  - A decorative background chapter-number digit (72pt) inflated the
    page-0 big-font tier's size threshold above the real title's own size
    (23-30pt), so the tier found nothing even on chapters where the real
    title *was* the biggest real text on the page — fixed by excluding
    bare-digit and single-character lines from the threshold computation
    (generalized further after finding one book's decorative digit
    renders through a custom font whose character code isn't even a
    recognizable ASCII digit).
  - This book's opener layout sometimes puts the real title on **page 1**
    instead of page 0 (page 0 is a "Notes for the Teacher" preface with no
    title on it at all) — the page-0-only big-font tier never looked at
    page 1; added the same big-font fallback one page later.
  - This book's own running-header text is *itself* printed corrupted,
    missing its own leading character on every single occurrence
    ("EVELOPMENT" for "DEVELOPMENT", all 7 times it appears as a running
    header — the intact word never appears as a header anywhere in the
    book). No frequency-based vote among running headers alone can
    recover this; fixed by cross-checking the tier-1 (running-header)
    winner against the page-0/1 big-font banner candidates for an exact
    single-leading-character truncation, preferring the fuller reading
    when found.
  - Net result: chapter 8 ("Development") now fully correct; chapters 9
    and 11 improved from honest "Unknown" to real (if abbreviated) titles
    ("Sectors", "Globalisation" — the book's own fuller titles are
    "Sectors of the Indian Economy" and "Globalisation and the Indian
    Economy"); chapters 10 and 12 remain garbled ("Oneyandredit",
    "Onsumerights" — real titles "Money and Credit", "Consumer Rights").
    These two are a **different, more severe corruption** in the same
    book: the page-1 banner text itself has characters missing at
    multiple, inconsistent positions (not just a single dropped leading
    character), so the same cross-check can't recover it — accepted as a
    genuine, documented limitation rather than forcing a fix that could
    produce a confidently-wrong title.
- **Superscript ordinal suffixes glued into a date-range title** (found
  independently of the 4-sub-book investigation, while auditing classes
  6-8's Social Science books more broadly per the same "check every
  chapter" request): a title like "13th to 17th Centuries" or "11th and
  12th Centuries" renders its "th" suffix as its own small, slightly
  raised span. Two distinct bugs compounded on this pattern:
  - The color-banner run-merger's row-detection compared each new span's
    y-position against the *whole accumulated merged region's* top edge
    (pinned to the first line forever once a second line merges in)
    instead of the last individual line actually seen — so a second
    fragment on an already-merged wrapped line failed the same-row check
    and broke the merge early, scattering the title into disconnected
    pieces. Fixed by tracking the last row's own y0 separately from the
    accumulated region's bounding box.
  - The page-0/1 big-font tier's leading-digit-strip (meant to remove a
    glued chapter number, "1PATTERNS IN" -> "PATTERNS IN") also stripped
    the leading digits off a genuine ordinal number in the title itself
    ("11thand 12th Centuries" -> "thand 12th Centuries") — fixed by only
    stripping when the text right after the digits does *not* start with
    a lowercase ordinal suffix ("st"/"nd"/"rd"/"th"); a glued chapter
    number is never followed by a lowercase "th" at that exact position
    in this corpus's designs, so the case-sensitive check disambiguates
    safely.
  - A remaining cosmetic gap — the source PDF sometimes has no space
    glyph between the ordinal suffix and the next word at all
    ("11thand") — fixed with a final-normalization step inserting a space
    after a digit+ordinal-suffix pattern when immediately followed by a
    lowercase letter.
  - Also fixed a lone recurring section-feature label sharing a page with
    the real title ("Big Questions", split across two font sizes so only
    its first word cleared the big-font threshold) bleeding its first
    word into the assembled title ("Landforms and Life Big") once the
    label's full text was excluded but this per-line scan still only ever
    saw the fragment — fixed by dropping any line that's a whole-word
    prefix of an excluded title.
  - Net effect across classes 6-8 Social Science: this single-book "Big
    Questions" pattern, once combined with the digit-threshold and
    single-char fixes above, recovered every chapter that previously
    fell back to honest "Unknown" for this reason (see updated totals
    below) — not a class-10-only issue, the "4 architectures" framing
    generalizes to "however many textbooks a class's Social Science is
    actually bundled from," which varies class to class.

**Updated final corpus-wide result (2026-09-01), full 204-chapter scan
after all 3b fixes, superseding 3a's table:**

| Design group | Chapters | Honest "Unknown" | Remaining wrong/partial titles |
|---|---|---|---|
| Maths (5 textbooks) | 55 | 0 | 0 |
| Science (5 textbooks) | 63 | 0 | 0 |
| Social Science, classes 6-9 | 58 | 0 | 0 |
| Social Science, class 10 (4 sub-books) | 22 | 0 | 4 (Economics sub-book only) |
| **Total** | **204** | **0** | **4** |

The 4 remaining are chapters 9, 10, 11, 12 of class 10 Social Science
(the Economics sub-book): 9 ("Sectors") and 11 ("Globalisation") are
real but abbreviated; 10 ("Oneyandredit") and 12 ("Onsumerights") are
still garbled by the multi-position character loss described above. This
is the full extent of the known-wrong list now — every other case listed
in 3a's "remaining known-wrong titles" table has been fixed by one of the
changes above.

Applied to the live Chroma corpus via `patch_chapter_metadata.py --all`
(739 of 2927 chunks corrected in this pass, matched by the reliable
`source_file` field). Full backend test suite (139 tests) still passes.

### 3c. Follow-up (2026-09-02, same day): the remaining Economics titles, plus a previously-undetected History bug, both found by the user reporting wrong chapter names in the app

The user reported the class 10 Social Science Economics chapters (all
but "Development") and the first History chapter were showing wrong
names — confirming the 4 Economics titles 3b left as known-wrong above,
plus a genuinely new find: **every "first chapter of a Section" in the
History sub-book (13, 15, 17) was showing that Section's own descriptive
theme name instead of its actual chapter title** ("Events and Processes"
instead of "The Rise of Nationalism in Europe", etc.) — not previously
caught because 3b's audit table showed 0 remaining wrong titles for
History, meaning this specific failure mode had never been checked for.

**History root cause:** this book groups its 5 real chapters into 3
numbered Sections, each opening with its own divider page ("SECTION I" +
a large descriptive theme name, e.g. "EVENTS AND PROCESSES") — and for
the FIRST chapter for two Sections, the following page is blank (just a
"Reprint 2026-27" printer's watermark) with the chapter's own real title
only appearing on the page after that. `_big_font_title` (the page-0/1
big-font fallback tier) had no way to recognize a "SECTION" divider page
as such, so the Section's theme name won outright as if it were the
chapter's own title; a stray-page fallback then also picked up the
watermark as a false candidate the same way. Fixed with three additions,
all in `schema.py`:
- `_big_font_title` now also skips a bare "SECTION [roman numeral]"
  divider label (same reasoning as the pre-existing bare "CHAPTER"
  skip) and a "Reprint <year>" printer's watermark, so neither
  contributes to a page's candidate text.
- A new, independent check: if page 0's own raw lines contain a
  "SECTION [roman numeral]" divider marker anywhere, `page0_title_candidate`
  is discarded outright (not just have the marker word stripped from
  it) — the Section's remaining descriptive theme name is still real,
  title-shaped text that would otherwise win, so the whole page-0
  candidate must be treated as if nothing were found there, cascading to
  page 1 then page 2.
- A new page-2 fallback tier (mirroring the existing page-0 -> page-1
  one), for the case where page 1 is also a dead end.

**Economics root cause (the 4 chapters left open in 3b):** two distinct,
narrower issues once actually root-caused:
- Chapter 9 ("Sectors" instead of "Sectors of the Indian Economy"): the
  real title renders as "SECTORS" at 46pt directly above "OF THE INDIAN
  ECONOMY" at 26pt — a 0.565 size ratio, under `_big_font_title`'s 0.7
  "same title, keep it" cutoff, so the second line never joined the
  first. Not fixed in code (lowering a shared, heavily-tuned corpus-wide
  ratio carries real regression risk for a single-chapter case) —
  corrected directly in `chapter_titles_final.tsv`, the same "known-good
  cache" mechanism `patch_chapter_metadata.py --all` already trusts for
  cross-chapter-collision titles.
- Chapters 10-12 ("Oneyandredit"/"Globalisation"/"Onsumerights"): the
  book's own running header text is itself corrupted throughout — e.g.
  "MONEY AND CREDIT" prints as "ONEYANDREDIT" on every single page of
  that chapter (both words lose their own leading letter — the same
  letters that render as enlarged drop-caps on the chapter's own big
  title banner — then get joined with no space). The existing "fuller
  reading" cross-check (built for a *single* dropped leading character,
  e.g. "DEVELOPMENT" -> "EVELOPMENT") doesn't recognize this two-word,
  no-space pattern, so the corrupted header wins the frequency-based
  tier before the page-0/1 candidate (itself sometimes ALSO incomplete,
  e.g. missing "AND" for the same 0.7-ratio reason as chapter 9) gets a
  chance. Generalizing the cross-check to reliably undo an arbitrary
  per-word leading-letter drop was judged too fragile for how narrow the
  payoff is (3 chapters in one book) — corrected directly in
  `chapter_titles_final.tsv` instead, same as chapter 9.

Verified via a full-corpus `extract_chapter_meta()` run (all 5 classes,
all 3 subjects) before and after, diffed to confirm the `_big_font_title`
changes affected only the intended chapters. `chapter_titles_final.tsv`
corrected for all 7 chapters (9, 10, 11, 12, 13, 15, 17);
`patch_chapter_metadata.py --all` re-run (165 chunks patched). Verified
end-to-end via `app.catalog.list_chapters('10', 'Social Science')` — all
22 titles now correct. Full backend test suite passes (143 tests).

## 4. Chunk ID / reference-corpus ID collisions (fixed, noted for history)

**What:** two different books were found, mid-corpus-ingestion, to reuse
a numbering scheme for genuinely different content:
- A Social Science chapter (class 10, chapter 15) had two unrelated
  topics both numbered "2.4" (adjacent to each other) — likely a numbered
  source-citation box colliding with real section numbering.
- A Maths book (class 6) had exercise questions where `(topic_number,
  question_number)` wasn't unique within a chapter.

Both caused a `DuplicateIDError` on upsert, caught immediately by
Chroma's own uniqueness check rather than silently overwriting data.

**Fix:** chunk IDs now key off each topic's *ordinal position* in the
chapter (detected via `chunk_index` resetting to 0, which reliably marks
a new topic regardless of what its printed number is) instead of the
printed topic number; reference-corpus IDs now use a running index
instead of `(topic_number, question_number)`. Both collections were
wiped and cleanly re-ingested under the new ID scheme after the fix, so
no stale/duplicate entries remain from the old scheme.

## 5. Reference corpus: construction/drawing questions have no text answer

**What:** ~15 exercise entries in the class 6 Maths book (geometry/
construction chapters — "Draw a rectangle...", "Trace the figure...")
have no text after "Ans." in the source PDF.

**Why:** their official answer is a hand-drawn diagram, not text — this
is a real content characteristic, not a parsing bug. These entries are
now **excluded** from the reference corpus (an empty answer doesn't serve
PLAN.md section 6's "official answer text captured" goal), rather than
stored as useless empty-answer stubs.

## 6. Not investigated at corpus scale (would need per-chapter spot checks)

These were confirmed as real, minor issues on the original sample chapter
(class 10 Science, chapter 1) during tasks 3-5c, fixed there, but weren't
re-verified chapter-by-chapter across all 200 chapters given the scope of
this task batch:
- Rotated/vertical sidebar text (e.g. a "Do You Know?" tab) can come out
  character-reversed — flagged in task 5, not fixed (needs rotation-aware
  handling in `reconstruct_lines`).
- Figure-dependent sentence flagging (task 5a) and image-caption linking
  were validated on 2 chapters (30 figures total), not the full corpus.

## Tooling note

`batch_ingest.py` is resumable (`--start-index N` or `--skip-existing`)
and safe to re-run — `embed_and_store_pdf` always upserts. This
environment kills a single long-running background process after
roughly 200-240s regardless of the requested timeout (observed
repeatedly during task 11), so processing the corpus took ~20 chained
invocations at a ~180s internal time budget each rather than one
continuous run.

**Re-ingesting any subject's chunks (e.g. via `embed_and_store.py`
directly, as task 15's class 6 Social Science fix did) always needs a
follow-up `patch_chapter_metadata.py --all` run afterward.** Found in
production (2026-09-02): `embed_and_store.py` → `schema.build_chunks()` →
`extract_chapter_meta(doc)` extracts each chapter's title independently,
with no `exclude_titles` — the cross-chapter dedup pass that actually
produces correct titles for books with a recurring book/feature-level
label (section 3a's "Big Questions", "Contemporary India – II", etc.) only
runs inside `fix_chapter_titles.py`. Re-ingesting a subject through
`embed_and_store.py` alone silently regresses any of its chapters that
needed that exclusion back to the wrong shared label — found via the
`app/catalog.list_chapters`/`list_topics` output itself (multiple real
chapters merged into one bogus "Big Questions" chapter bucket) right
after re-ingesting class 6 Social Science's 14 chapters. Fixed by
re-running `patch_chapter_metadata.py --all` (safe and idempotent — it
reads the authoritative `chapter_titles_final.tsv`, matched by the stable
`source_file` field) immediately after any subject re-ingestion.
