# PLAN.md — Section: Chunking NCERT Content for RAG

Scope: this plan covers only the content-ingestion/chunking section of the
project (SPEC.md sections 5–6). It turns "topic-based chunks, tagged with
class/subject/chapter/topic, embedded and retrievable" into a concrete
build plan. No implementation code — that comes in the Implement step,
one task at a time.

## 1. Goal (recap, in plan terms)

Given a folder of NCERT PDFs, produce a set of **topic-level chunks**, each:
- Semantically complete (doesn't cut a concept in half)
- Tagged with `class`, `subject`, `chapter`, `topic` metadata
- Embedded and stored in a vector database
- Retrievable by metadata filter + semantic similarity, scoped exactly to
  what a quiz request asks for (a single topic, a whole chapter, or a
  whole subject)

## 2. Technical approach

**Step A — Structure-aware parsing.**
Extract text from each NCERT PDF using a library that preserves heading/
formatting information (not a flat text dump), so topic subheadings within
a chapter can be programmatically detected.

**Step B — Topic boundary detection.**
Use each chapter's own subheadings (matching the chapter's index/table of
contents) as chunk boundaries. A chunk = the text between one subheading
and the next.

**Step C — Handle size exceptions.**
- Short topics (a paragraph or two): kept as-is, no merging.
- Long topics (multiple pages): split further using sub-subheadings if
  present; otherwise split by paragraph groups, but all resulting pieces
  keep the *same* topic metadata plus a `chunk_index` so they're treated as
  one topic at retrieval time.

**Step C2 — Handle non-prose content within a topic.**
NCERT chapters mix plain paragraph text with images, tables, and the
book's own in-text exercise questions. Each needs different handling:

- **Images/diagrams:** not used for question generation in v1 (SPEC.md
  non-goal), but **not discarded** — the image file and its caption text
  are extracted and stored, linked to the topic/chunk they appeared in,
  so this data is ready for a future multimodal/image-question feature
  without needing to re-parse the source PDFs. The image itself is
  excluded from the `text` field used for embedding/grounding. Sentences
  whose meaning depends on seeing a figure (detected via phrasing like
  "as shown in," "in the figure below," "the diagram illustrates") are
  flagged as **not usable for question generation**, even if left in the
  chunk's `text` for surrounding readability/context.
- **Tables:** extracted separately using table-aware parsing, not flattened
  into plain paragraph text (which jumbles rows/columns). Stored in the
  chunk in a clean structured form (e.g., markdown-table formatting) and
  tagged `has_table: true`, since well-structured tables are strong source
  material specifically for matching-type questions.
- **In-text exercise questions** ("Check your understanding," boxed
  questions between topics, numbered "Q1./Q2." blocks): detected via their
  typical formatting/heading markers and **excluded from the chunk content**
  used for grounding — the underlying content is the book's own
  pre-written questions, not facts, and including them risks the LLM
  copying them rather than generating new questions from the underlying
  content. They are **not discarded**, though — they're extracted and
  stored separately (own table, not mixed into topic chunks), tagged by
  class/subject/chapter/topic, as a future reference corpus for
  exam-style question generation (SPEC.md section 13, deferred v2
  feature). Previous CBSE question papers, once sourced, would be
  captured and tagged the same way.

**Step D — Metadata tagging.**
Every chunk is stored with: `class`, `subject`, `chapter`, `topic`,
`chunk_index` (0 if not split further), and the raw chunk text.

**Step E — Embedding.**
Each chunk's text is embedded using a sentence-embedding model.

**Step F — Storage.**
Chunk text, metadata, and embedding vector are stored together in a vector
database that supports **metadata filtering combined with similarity
search** (not similarity search alone).

**Step G — Retrieval-quality testing.**
Before this feeds into question generation (a later section), manually
verify retrieval accuracy on a sample of known topics.

## 3. Proposed tech stack (with reasoning)

| Piece | Choice | Why |
|---|---|---|
| PDF parsing | **PyMuPDF (`fitz`)** | Preserves font size/style info, which helps detect headings vs. body text — more reliable than plain-text extraction for structure detection. |
| Heading/topic detection | Custom logic on top of PyMuPDF output (font size/boldness heuristics + matching against the chapter's index text) | NCERT PDFs don't have a standard machine-readable structure tag, so this needs a heuristic pass rather than a built-in library feature. |
| Embedding model | **`sentence-transformers` — `all-MiniLM-L6-v2`** (local, free) | Good enough separation for distinct textbook topics; no per-call API cost, so re-embedding during development is free and fast. |
| Vector database | **Chroma** (local, file-based) | Free, simple to set up for a project this size, supports metadata filtering + similarity search together, no separate server to manage during development. |
| Language/runtime | **Python** | Matches the rest of the stack (generation, grading) already planned for later sections. |

*(These choices are for v1/development. Nothing here blocks swapping to a
hosted vector DB or a larger embedding model later if retrieval quality
turns out to need it — see section 6, Verify.)*

## 4. Data model for a chunk

Each stored chunk record needs:

```
chunk_id        (unique identifier)
class           (e.g., "8")
subject         (e.g., "Science")
chapter         (e.g., "Photosynthesis in Higher Plants")
chapter_number  (for ordering/coverage-rule counting, per SPEC 10a)
topic           (e.g., "Site of Photosynthesis")
topic_number    (for ordering/coverage-rule counting, per SPEC 10a)
chunk_index     (0 by default; >0 only if a topic was split further)
text            (the raw chunk content, images stripped, exercise
                 questions excluded, tables in structured form)
has_table       (true/false — signals good source material for matching
                 questions)
figure_dependent_spans (list of sentence-level spans flagged as relying on
                 an image, excluded from question-generation source even
                 though kept in `text` for context)
images          (list of {image_file, caption} — stored for future use,
                 not used in v1 generation)
embedding       (vector, generated in Step E)
```

A **separate table** (not part of the chunk record above) stores in-text
exercise questions and, once sourced, previous CBSE question papers —
each tagged with `class`, `subject`, `chapter`, `topic`, the question
text, and the **official answer/solution text as printed in the source
PDF** (where the book provides one). This is the future reference corpus
for exam-style generation (deferred v2 feature, SPEC.md section 13); it
is not queried by v1's question-generation pipeline, but the stored
answer text/formatting **is** used starting in v1 as a style reference for
the post-quiz solutions feature (SPEC.md section 8a).

`chapter_number` and `topic_number` matter beyond display — SPEC 10a's
coverage rule ("question count > number of topics/chapters") needs a
reliable count per chapter/subject, so this structure should make
"how many topics does chapter X have" a simple count query, not something
recomputed by re-parsing the book.

**Important:** `chapter` and `topic` (the real textbook names) are the
identity fields — used for retrieval, metadata filtering, and everything
shown in the UI. `chapter_number` and `topic_number` are internal-only,
used solely for ordering and the coverage-rule count in section 10a. They
must never be displayed to the user or used as the way a chapter/topic is
looked up or referred to (SPEC.md section 5).

**Source-file naming caveat:** local PDF files may be named generically for
storage convenience (e.g., `Chapter01.pdf`, `Chapter02.pdf`) — this is fine
as a filesystem convention only. The actual `chapter` name stored in the
data model must always be **extracted from the PDF's own content** (its
title page or table of contents during Step A/B parsing), never derived
from or assumed equal to the filename.

## 5. Constraints carried over from SPEC.md

- Chunking must be **topic-based, not fixed-size** (SPEC section 6).
- Every generated question must later be traceable back to the exact
  chunk(s) it came from (SPEC section 6) — so `chunk_id` needs to be
  something the generation step can reference and store alongside each
  question in the bank.
- This step has no direct user-facing behavior, but its output quality
  directly determines whether later sections (retrieval, generation,
  grounded explanations) work correctly — so retrieval-quality testing
  (Step G) is not optional polish, it's a hard gate before moving on.

## 6. Task breakdown (small, independently reviewable tasks)

0. **Source and organize NCERT PDFs** — download official PDFs (from
   ncert.nic.in) for the starting class/subject scope, organized in a
   consistent folder structure (e.g., `class/subject/chapter.pdf`) before
   any parsing work begins.
1. **Set up project structure and dependencies** — Python environment,
   PyMuPDF, sentence-transformers, Chroma installed and importable.
2. **Extract raw text + formatting metadata from one sample NCERT PDF** —
   confirm font-size/style info is actually usable for heading detection
   on real NCERT formatting before building anything else on top of it.
3. **Build heading/topic-boundary detection logic** on that one sample
   chapter — output should be a list of (topic name, topic text) pairs.
4. **Validate detection against the chapter's actual index/table of
   contents** for that sample chapter — do detected topics match the
   book's own listed topics? Fix heuristics until they do.
5. **Handle the long-topic split case** — implement the sub-subheading (or
   paragraph-group) fallback splitting from Step C, tested on one chapter
   known to have a long topic.
5a. **Extract and store images/captions, and flag figure-dependent
    sentences** (Step C2) on the sample chapter — confirm image/caption
    extraction links correctly to the right chunk, and the
    phrasing-based figure-dependency detection catches obvious cases
    without over-flagging normal sentences.
5b. **Extract tables separately in structured form** (Step C2) — test on
    a chapter known to contain at least one table.
5c. **Detect, exclude from grounding, and separately store in-text
    exercise questions along with their official printed answers**
    (Step C2) — test on a chapter known to contain "Check your
    understanding"-style boxes with provided answers; confirm both
    question and answer land in the separate reference-corpus table,
    correctly tagged and paired.
6. **Define and implement the chunk metadata schema** (section 4) in code,
   including `has_table`, `figure_dependent_spans`, and `images`, plus the
   separate exercise-question/CBSE reference table.
7. **Add embedding generation** for a chunk's text using the chosen model.
8. **Set up Chroma storage** with metadata fields as filterable attributes,
   and store the sample chapter's chunks end-to-end.
9. **Write a retrieval test script** — given a (class, subject, chapter,
   topic) tuple, confirm the correct chunk(s) come back; given a chapter
   with "all topics," confirm every topic's chunks come back.
10. **Scale the pipeline to a full subject** (all chapters in one
    class+subject) and re-run the retrieval test script across it.
11. **Scale to all target classes/subjects** once the pipeline is proven
    reliable on one full subject.
12. **Document known parsing exceptions** — any chapters where heading
    detection, table extraction, or exercise-question filtering needed
    manual correction, so this is tracked rather than silently inconsistent.
13. **(2026-08-31 follow-up)** A user-reported bug ("class 10 Maths and
    Social Science chapter names are not there") turned into a full
    corpus audit of chapter-title extraction, since task 12's spot-checks
    had missed that "chapter title is reliable" didn't actually hold
    across a large minority of the corpus. The user explicitly requested
    per-textbook-design extraction logic rather than one generic
    heuristic, given the corpus spans genuinely different production eras
    and Social Science splits into per-discipline sub-books — this
    converged on one much stronger shared signal (a title's display color
    differs from body text, reliably, across every era tested) rather
    than 18 hand-written extractors, organized as a tiered pipeline plus
    a cross-chapter dedup pass. See `ingestion/PARSING_EXCEPTIONS.md`
    section 3a for the full writeup, including ~10 distinct bugs found
    and fixed while hardening it, 2 regressions caught along the way, and
    the corpus-wide numbers at that point (all of Maths (55/55) and
    Science (63/63, 1 honest "Unknown") verified correct and patched into
    the live corpus; the large majority of Social Science too; ~15
    chapters across a handful of specific Social Science books still
    wrong).
14. **(2026-09-01 follow-up)** User pushback on task 13's "~15 residual,
    not chased further" — correctly pointing out that a class's Social
    Science is bundled from several distinct textbooks (4, in class 10's
    case) each with its own design, and that the residual should be
    diagnosed per sub-book rather than treated as unrelated one-offs.
    Re-investigated on that basis: nearly all of the residual turned out
    to share a handful of real, book-specific root causes (a Geography
    book's page-number-glued-to-header text, a History book's structural
    "SECTION" divider label, an Economics book with several stacked bugs
    including a title split across page 0 and page 1, and a
    superscript-ordinal-suffix span-merge bug affecting several classes'
    books at once), each fixed with a change scoped to that specific
    pattern. See `ingestion/PARSING_EXCEPTIONS.md` section 3b for the
    full writeup and updated numbers: honest "Unknown" count dropped from
    18 to **0** across the whole 204-chapter corpus; only 4 chapters (all
    in class 10's Economics sub-book, itself affected by a severe,
    multi-position source-PDF character-loss issue) remain wrong or
    abbreviated. Re-patched into the live Chroma corpus (739/2927 chunks
    corrected in this pass); full backend test suite (139 tests) still
    passes.
15. **(2026-09-02 follow-up) Topic/subtopic extraction, not just chapter
    titles.** User feedback made three rules mandatory going forward (see
    SPEC.md section 6): only real topics are ever stored/surfaced (a large
    topic's own subtopics chunk-and-chain under it, never become sibling
    topics); content before the first topic heading is named "Introduction"
    literally, not a placeholder; and topic/heading detection is verified
    and built per textbook, going one textbook at a time, starting with
    class 6 Social Science.

    Investigation found the existing Social Science fallback
    (`_detect_topics_allcaps_fallback`, built for the older ALL-CAPS-heading
    book design) doesn't just risk subtopic-pollution on this book — it
    fails outright, since this revised-curriculum book's real headings are
    Title Case, not ALL-CAPS. The one all-caps string it does match
    ("THINK ABOUT IT", a callout-box label) becomes a fake topic that
    swallows the rest of the chapter. This book's actual hierarchy is
    font-size-tiered (verified across all 14 chapters): 17pt = real topic,
    15pt = subheading-within-topic, 14pt = callout-box label (not a heading
    at all) — color is chapter-specific, not a stable signal, unlike size.

    Also decided, per user direction: "LET'S EXPLORE"/"THINK ABOUT IT" boxes
    (activity/reflection prompts, no facts) are dropped from grounding;
    "DON'T MISS OUT" boxes (real supplementary facts) stay in the enclosing
    topic's text; the "Before we move on ..." end-of-chapter recap (spans
    multiple topics) is dropped; the "Questions, activities and projects"
    exercises section (mostly no printed answer, unlike the Maths book) is
    kept as a new `reference_corpus` use case — a wording-style example for
    *generation* prompts (not the existing solutions-explanation use case,
    which needs a real Q+A pair) — requiring `reference_corpus.answer` to
    become nullable and a new `source = 'ncert_activity'` tag, wired into
    `prompts.py`'s generation templates and excluded from
    `explanations.py`'s existing style-reference query. Decorative
    map/diagram label text (e.g. "HINDU KUSH" on a map graphic) coincides
    with heading-adjacent font sizes; filtered by rejecting ALL-CAPS
    candidates outright, since this book's real headings are never
    ALL-CAPS (unlike the older SS design this fallback was built for).

    **Result:** 13/14 chapters produce a clean topic list (no subtopic
    pollution, no placeholder names); one known residual (chapter 1, an
    already-atypical bundled book-intro/chapter-1 file, has one garbled
    topic from an unrelated decorative-text collision). Re-ingested into
    the live Chroma corpus. Also found and fixed a genuinely separate,
    pre-existing gap while wiring the exercises feature: Postgres's
    `reference_corpus` table had zero rows — `embed_and_store.py` only
    ever wrote to Chroma, nothing synced it to Postgres, so
    `explanations.py`'s solutions-style-reference feature had been
    silently inert for every subject since it was built. Fixed with
    `backend/scripts/sync_reference_corpus.py` (366 rows synced: 275
    pre-existing + 91 new). Full backend test suite (139 tests) passes.
    See `ingestion/PARSING_EXCEPTIONS.md` section 2a for the full writeup.

    **(2026-09-02, same-day continuation) Class 7 Social Science.** Same
    font-size-tiered design confirmed across all 20 chapters, needing two
    small additions (a "LET'S REMEMBER" activity-box label; an
    exercises-header variant, "Questions and activities" vs. class 6's
    "Questions, activities and projects") plus one real bug fix: a
    lettered subheading marker ("b) Indian railway network") rendered at
    topic font size in one chapter while its siblings (a/c/d/e) correctly
    rendered at subheading size — a source-PDF inconsistency, not a
    different convention, that was promoting a subtopic to its own topic
    (exactly the Rule 1 violation this whole effort exists to prevent,
    from a new root cause). Fixed generally: a bare lettered-list marker
    now forces subheading tier regardless of measured size, and a heading
    candidate already accumulating within one PyMuPDF block now extends
    across further same-block lines regardless of their individual size
    (a real heading wrap never mixes tiers mid-wrap). **Result:** all 20
    chapters produce a clean topic list (better than class 6 — no
    residual). Re-ingested (283 chunks replacing 215); reference_corpus
    resynced (530 rows, up from 366). Full backend test suite (139 tests)
    passes. See `ingestion/PARSING_EXCEPTIONS.md` section 2b.

    `schema.py`'s `_REVISED_CURRICULUM_SS_CLASSES` allowlist was `{"6", "7"}`
    at this point; see below for class 8.

    **(2026-09-02, same-day continuation) Class 8 Social Science.** Same
    design confirmed across all 15 chapters, no new box labels or
    exercises-header variants needed. One bug found: a chapter title with
    a date range ("Cultural Currents: 13th to 17th Centuries") renders its
    two superscript "th" suffixes at the same row position on page 0,
    which this pipeline's row-based glyph-dedup (built for NCERT's
    fake-bold duplicate-render trick, where a same-position repeat really
    is a duplicate) merges into one line reading "thth" — landing in this
    book's topic-size range with lowercase letters, it passed every
    existing check and became a bogus topic swallowing the entire
    Introduction. Same root-cause class as the chapter-title fix (section
    3a) and 2b's lettered-marker fix, but a fresh instance in this
    separate code path. Fixed by rejecting any bare ordinal suffix
    ("st"/"nd"/"rd"/"th"), including concatenated copies, as heading text
    outright. **Result:** 15/15 chapters produce a clean topic list.
    Re-ingested (206 chunks replacing 199); reference_corpus resynced (682
    rows, up from 530). Full backend test suite (139 tests) passes. See
    `ingestion/PARSING_EXCEPTIONS.md` section 2c.

    `schema.py`'s `_REVISED_CURRICULUM_SS_CLASSES` allowlist was
    `{"6", "7", "8"}` at this point; see below for class 9.

    **(2026-09-02, same-day continuation) Class 9 Social Science — last
    book in this production family.** Verified across all 9 chapters. A
    differently-designed title page (page-0 elements split into spans
    differently than classes 6-8) needed no fix, since every element
    lands safely outside both heading-size ranges regardless; the
    callout-box system doesn't appear to be used in this book at all. One
    new failure mode: a heading sitting at the bottom of a page directly
    above a table (whose own text reads out of order ahead of it — the
    same pre-existing table-layout limitation already documented for
    `topic_page_ranges`) had nothing after it but a running-header line
    before the next real topic. Fixed by raising the existing empty-topic
    filter from "non-empty" to a minimum content length (80 characters,
    confirmed safe by re-running the full regression on classes 6-8 with
    zero change in output). **Result:** 9/9 chapters clean. Re-ingested
    (100 chunks replacing 136); chapter titles needed no correction this
    time; reference_corpus resynced (783 rows, up from 682). Full backend
    test suite (139 tests) passes. See
    `ingestion/PARSING_EXCEPTIONS.md` section 2d.

    `schema.py`'s `_REVISED_CURRICULUM_SS_CLASSES` allowlist is now
    `{"6", "7", "8", "9"}` — all 4 classes sharing this design are
    verified against the mandatory topic/subtopic rules.

    **(2026-09-02, same-day continuation) Class 10 Social Science — 4
    sub-books, 3 architectures, all fixed in one pass.** This class
    bundles History (numbered-heading path, already correct, no changes),
    Geography and Economics (the older ALL-CAPS fallback, which had never
    received any of the hardening built for classes 6-9 or for the
    chapter-title dedup work — fixed with duplicate-render collapsing,
    digit-suffix-aware running-header exclusion, structural-label
    filtering, a chapter-end terminator, line-wrap merging, and a
    per-chapter title-fragment exclusion), and Political Science (a
    third, previously undetected design — Title Case, not ALL-CAPS at
    all, so it had been silently falling back to one giant
    "Introduction" per chapter; fixed with a brand-new font-size-tiered
    detector, `detect_topics_ss_class10_polsci`, dispatched via an
    explicit chapter-number allowlist,
    `_CLASS10_POLSCI_CHAPTERS = {"18","19","20","21","22"}`). **Result:**
    History 5/5 and Political Science 5/5 clean; Geography 5/7 clean (2
    chapters have no detectable heading signal at all); Economics 5/5
    with 0-1 minor documented residual each. Re-ingested (259 chunks
    replacing 353); chapter titles re-patched (109 chunks); full backend
    test suite (139 tests) passes. No exercise-capture/reference_corpus
    wiring was built for this older design — out of scope for a
    topic-structure audit. See `ingestion/PARSING_EXCEPTIONS.md`
    section 2e.

    **(2026-09-02, same-day continuation) Maths and Science — five real
    bugs found and fixed in the shared numbered-heading production path
    (`extract_exercises.clean_topics()`), none related to wrong
    architecture (the "N.M Heading" design is correct for every one of
    these books) but all shipping wrong data regardless:** (1)
    false-positive topic fragmentation from decimal numbers in body
    text/tables (temperature/measurement values) coincidentally matching
    the heading shape at sizes above the old single global
    `MIN_HEADING_SIZE=12.0` floor — fixed by calibrating each chapter's
    real heading size empirically instead of trusting one constant across
    books whose real heading sizes range 12-17pt; (2) a decorative
    "activity" icon glyph (a raw `\x07` control character) leaking into
    topic names, pervasive in class 8 Science — fixed by stripping
    control characters; (3) one real topic's content silently merged into
    its neighbor because its own heading was punctuated "2.4." instead of
    "2.4" (class 9 Maths ch2) — fixed by tolerating an optional trailing
    period, safe because fix (1)'s size gate still applies; (4)
    title-page furniture (a stray digit, a repeated chapter number/title)
    stored as "Introduction" down to a single stray character — fixed
    with the same 80-char minimum already used for Social Science; (5)
    class 10 Science chapter 7's title extracted as "B" (a decorative
    drop-cap first letter rendering at the same size as the real title) —
    fixed by adding the same single-character guard `_big_font_title`
    already had to the shared `_looks_like_title_candidate`. All five
    verified against previously-correct chapters with zero regressions,
    and cross-checked against the existing `chapter_titles_final.tsv`
    (confirmed "B" was the only 1-2 character title in the whole corpus).
    **Result:** all 131 Maths+Science chapters (5 classes × 2 subjects)
    re-ingested (1866 chunks, down from 1933 — net pollution removed);
    `chapter_titles_final.tsv` corrected for the "B" title and
    `patch_chapter_metadata.py --all` re-run; `sync_reference_corpus.py`
    re-run (783 rows, unchanged — that extraction path was untouched by
    these fixes); verified end-to-end via `app.catalog`; full backend
    test suite passes (143 tests). See `ingestion/PARSING_EXCEPTIONS.md`
    section 2f.

    This closes out "continue with class 10 social science, science,
    maths" — every subject/class in the corpus has now been individually
    audited against the mandatory topic/subtopic rules at least once.

    **(2026-09-02, same day, third pass) The user reviewed the app
    directly and found three more real bugs in class 10 Social Science —
    History, Geography, and Economics each needed a genuinely different
    fix:**
    - **History:** subsections ("N.M Title", 12pt) were being promoted to
      sibling topics — the real design is bare "N  Title" (18pt) for a
      topic, "N.M" for a subsection chained under it. Built a new
      dedicated detector, `detect_topics_ss_class10_history()` (font-
      size-tiered, SUBHEADING_MARKER-chained, same family as the
      Political Science detector), dispatched via an explicit chapter
      allowlist. Result: 6/4/4/6/9 real topics per chapter, zero
      subsections promoted.
    - **Geography:** discovered this book renders every page as two
      side-by-side text columns, and PyMuPDF's own block order doesn't
      reliably read left-then-right — one page returned the entire right
      column before the entire left column, silently corrupting reading
      order for every downstream detector. Fixed by reordering blocks
      (left-column blocks before right-column blocks, a stable sort) in
      `all_pages_lines()` itself, gated to Geography's chapters only, so
      every existing function benefits automatically. Also fixed a
      compass-rose diagram's scattered direction labels winning as a
      bogus topic. **Known open item, not fixed:** chapter 6 (and
      possibly 4) may have further real subheadings marked by a
      bold+color signal spotted on a spot check, but that signal also
      marks ordinary emphasis elsewhere in the same book — needs more
      investigation than this pass had time for.
    - **Economics:** "Development" had 4 of its ~12 topics turn out to be
      table-caption fragments (table captions render at the same size as
      real headings; a wrapped caption's continuation line passed the
      heading shape check on its own). Also found the existing chapter-
      title-bleed exclusion was both too eager (a short, generic title
      like "Development" wrongly excluded real topics that share that
      common word) and, previously, not eager enough (chapters 9-12 never
      got their real title for exclusion purposes at all, since their own
      single-chapter extraction returns a shared "Notes for the Teacher"
      label instead — now fixed by consulting the cross-chapter-deduped
      title cache first). Result: "Development" now shows all 8 real
      topics; chapters 9-12 recover several previously-dropped real
      topics ("National Development", "Consumer Rights", etc.).
    - All 22 chapters re-ingested (105 chunks re-patched by
      `patch_chapter_metadata.py --all`); full backend test suite passes
      (143 tests). See `ingestion/PARSING_EXCEPTIONS.md` section 2g.

16. **(2026-09-03, documented retroactively 2026-09-08) Classes 11-12
    (Senior Secondary) added to the corpus.** PDFs for Biology, Chemistry,
    Maths, and Physics (classes 11 and 12) were sourced and ingested —
    Physics/Biology needed a new shared ALL-CAPS-numbered detector (real
    headings duplicated by a Title-Case sidebar mini-TOC, and a font-size
    floor low enough for this book's ~10pt real headings); Chemistry
    needed a dedicated chapter-8 detector plus corrupted-text handling for
    a genuine font-embedding defect in chapters 7-9; Maths needed two
    numbered-heading-path bug fixes (glued heading-number kerning
    artifacts; a garbled equation fragment gated out via a new
    dominant-leading-number calibration signal). This work was done the
    same way as tasks 13-15's per-textbook fixes, but — unlike every prior
    continuation of this task — was never written up here, in
    `PARSING_EXCEPTIONS.md`, or in the `[[rag-extraction-rules]]` memory
    at the time; found only by reading the code's own dated comments
    during an unrelated full-project review five days later. A post-hoc
    structural check (2026-09-08) confirms full chapter coverage (matching
    the source PDFs) and no "Unknown" titles or known pollution/garble
    signatures across all 8 subject bundles, but **this is not the
    per-chapter raw-span-dump audit against Rules 1/2/3 that classes 6-10
    received** — that remains open work for these 8 bundles, per Rule 3's
    "verified per textbook" standing requirement, not something to treat
    as already covered by task 15. Full writeup:
    `ingestion/PARSING_EXCEPTIONS.md` section 2h.

Each numbered task above is meant to be implemented and reviewed as its
own small diff, per the workflow — not built all at once.

---

# PLAN.md — Section 2: Question Generation, Bank & Duplicate Handling

Scope: this plan covers quiz generation end-to-end — from a student's quiz
request through bank lookup, LLM generation for any shortfall, duplicate
handling, and delivery — up through scoring and the post-quiz options
(SPEC.md sections 5 steps 1–12, 7, 8, 9, 10, 10a). It assumes Section 1's
RAG chunking pipeline is already working and queryable. No implementation
code — Implement step comes after this is reviewed.

## 1. Goal (recap, in plan terms)

Given a quiz request (class, subject, chapter(s)/topic(s), difficulty,
question count, question type(s)) for a specific user, produce exactly the
requested number of valid, non-duplicate, appropriately-distributed
questions — preferring reuse of already-validated bank questions the user
hasn't seen, and falling back to LLM generation (grounded in Section 1's
chunks) only for the shortfall — then deliver, grade, score, and record
the attempt.

## 2. Technical approach

**Step A — Resolve the request into scope.**
Translate the user's selections into a concrete set of (topic, difficulty,
question_type) allocations, applying:
- The coverage rule (SPEC 10a) if "all topics"/"all chapters" was chosen.
- Equal-weighting + round-robin remainder as the base distribution.
- Accuracy-weighted adjustment on top of that base, using the user's
  performance history (SPEC 11a) where enough data exists per sub-unit;
  random fallback where it doesn't.

The result of Step A is a concrete list like: `[(topic: "Site of
Photosynthesis", difficulty: "medium", type: "MCQ", count: 3), ...]` —
this is the unit of work for every step that follows.

**Step B — Bank lookup, per allocation line.**
For each (topic, difficulty, type, count) line: a single indexed
relational query against the `questions` bank that:
- Filters by the scope tags and `type`.
- Excludes any `question_id` in this user's `seen_questions`.
- Returns at most one question per `concept_cluster_id`
  (`DISTINCT ON (concept_cluster_id)`) — this enforces both same-type and
  cross-type near-duplicate exclusion in the query itself, with no
  embedding comparison at serving time.
Take up to `count` results. Record the shortfall (`count` minus what the
bank provided) for Step C. No over-fetch loop and no read-time similarity
pass are needed — the cluster-id precomputation (section 5) already did
that work at write time.

**Step C — Generate the shortfall.**
For any remaining shortfall per allocation line:
1. Retrieve the relevant chunk(s) for that topic from Section 1's vector
   store (metadata-filtered).
2. Call the LLM with a type-specific prompt template (see section 3),
   grounded in the retrieved chunk text, for the shortfall count.
3. Apply the reliability handling from section 4 (retries, fallback
   provider, schema validation) to each generated question.
4. Apply duplicate handling from section 5 before accepting a question.
5. Accepted questions are added to the bank (tagged with their source
   `chunk_id`, per SPEC section 6) and included in this quiz.

**Latency-critical execution rules (SPEC "latency target"):**
- **Parallel generation:** when more than one allocation line needs
  generation, fire those LLM calls **concurrently**, not sequentially, so
  total wait ≈ one call's duration, not the sum. (This is distinct from —
  and not the same as — the rejected "parallel provider racing"; here it's
  different questions generated at once against the primary provider.)
- **Non-blocking overgeneration:** the user's exact requested quiz is
  assembled and served as soon as the needed questions are ready. Any
  overgeneration to pre-fill the bank (SPEC section 7) happens in a
  **background task after the response is sent** — it must never sit on
  the user's request path. If background banking fails, it's a silent
  no-op (the user already got their quiz); log for later. **The
  overgeneration factor is a config value** (section 8) — start small or
  off on a tight LLM budget, dial up on a larger one.

**Step D — Assemble and deliver the quiz.**
Combine bank-sourced and freshly-generated questions per allocation line
into the final quiz, in the counts originally requested, and present it
to the user (SPEC section 5, steps 9–10).

**Step E — Score, record, and offer next steps.**
On submission: grade per SPEC section 8's rules, compute the overall
score, update the performance-history counters (SPEC 11a) grouped by
`(topic, question_type)`, and present the score plus **four options**
(SPEC section 5, step 11): go home, start a new quiz on the same topic
(new unseen questions), requiz with the same questions (replay — **not**
recorded to history, does not affect accuracy), or see solutions (SPEC
section 8a). Note the requiz path skips the history-update logic entirely.

## 3. Prompt design per question type

Each question type needs its own prompt template — a single generic
prompt won't reliably produce the right structure for all three:

- **MCQ:** grounded chunk text in, question + 4 options + correct option
  index out, difficulty-appropriate for the requested level.
- **Fill-in-the-blank:** grounded chunk text in, question + blank +
  correct answer (numeric or single text term, per SPEC section 8) out.
  Prompt must explicitly constrain the answer to a single term — not a
  phrase or sentence — since that's the grading contract.
- **Matching:** grounded chunk text in, a left-hand list and a right-hand
  list of equal length + the correct pairing out. Needs source content
  that actually has enough paired facts (e.g., terms↔definitions,
  events↔dates) — a chunk may not always support this, see section 6.

All three share: strict output must validate against a schema (section 4),
and every question must carry a reference to its source `chunk_id` for
later grounded explanations (SPEC section 8a).

**Concept-testing vs. verbatim recall (SPEC section 9a, added after live
testing surfaced it as a real problem):** all three prompts now include an
explicit instruction to test understanding of the concept using the
subject's standard terminology, phrased in the model's own words — not
lightly-reworded textbook sentences. The fill-in-blank prompt additionally
instructs the model to prefer instantiating computational concepts with
concrete numbers and a numeric expected answer, rather than asking for the
formula itself in words/symbols (e.g. "length × length" vs. a student's
equally-correct "(side)^2" — a phrasing mismatch a similarity-based grader
can't reliably resolve either way). Enforced two ways:
1. Prompt instruction (best-effort, the model may still slip).
2. A schema-level backstop: `FillInBlankPayload` rejects any answer
   containing an algebraic operator (`^`, `×`, `*`, `/`, `=`, parentheses)
   or a "word × word" pattern (e.g. "side x side"), which fails validation
   and triggers the existing section 4 retry path — same mechanism already
   used for malformed JSON, no new failure-handling code needed. A bare
   fact/term/notation answer (a chemical formula, a named process) is
   unaffected since it has no operator.

## 4. Reliability handling (per SPEC.md section 9)

- **API failure:** retry with backoff (bounded attempts), then fall back
  to a secondary LLM provider (sequential, not parallel — SPEC section 9),
  then surface a clear error if both fail.
- **Malformed output:** validate against a Pydantic schema per question
  type; on failure, regenerate (bounded retries).
- **Persistent failure on one question:** drop it, generate a
  replacement, so the requested count is still met.

## 4a. Data model and database architecture

**Database choice: PostgreSQL + the `pgvector` extension (single database).**
Rationale (from real-world practice): keeping a separate relational DB and
vector DB in sync is a common source of latency spikes and bugs, and this
app's scale (classes 6–10, a bank growing to tens of thousands of
questions — not billions) does not need a dedicated vector store. pgvector
holds relational data and embeddings together with transactional
consistency (a question, its embedding, and its cluster id are written
atomically) and one query engine for both scoped relational filtering and
the write-time ANN search. HNSW indexing in pgvector is fast enough at
this scale, and the ANN search runs only on the write path.

**Write-time vs. read-time split (core latency principle):** all
similarity/embedding work happens at write time (question intake — already
a background operation) so the serving path is pure indexed relational
lookup. See section 5 for how this eliminates read-time similarity math.

Key tables/fields:
- **`questions` (bank):** `question_id`, the scope tags (`class`,
  `subject`, `chapter`, `topic`, `difficulty`, `type`), `text`,
  `source_chunk_id` (for grounded explanations), `text_hash` (normalized
  SHA-256, exact-dup detection), `embedding` (pgvector), and
  **`concept_cluster_id`** (assigned at write time — the single mechanism
  for both same-type and cross-type near-duplicate handling).
- **`seen_questions`:** `(user_id, question_id, delivered_at)` — one row
  per question delivered to a user; written at Step D, read at Step B.
- **`performance_counters`:** primary key `(user_id, topic,
  question_type)` with `correct_count`, `total_count` (SPEC 11a).
- **`quiz_attempts` / `attempt_answers`:** per-attempt record and
  per-question results (excludes requiz replays — SPEC section 11).

**Serving-path indexes (no vector touched at read time):**
- Composite index on `questions (class, subject, chapter, topic,
  difficulty, type)` — scope filtering.
- Index on `seen_questions (user_id, question_id)` — exclude-seen.
- Index on `questions (concept_cluster_id)` — the `DISTINCT ON` that
  enforces cross-similarity for free.
- Primary-key lookup on `performance_counters` — weak-topic weighting.

## 5. Duplicate handling — precomputed at write time (per SPEC.md section 7)

All similarity work happens at question intake (a background operation off
the request path), so serving stays a pure relational lookup.

- **At intake, per new validated question:**
  1. Compute `text_hash` (normalized SHA-256) — exact-duplicate check
     against the same scope; if matched, discard.
  2. Compute embedding; run pgvector ANN search against existing bank
     questions in the same topic + difficulty, within a **restricted
     comparison group**: MCQ + fill-in-blank cluster together (single-fact
     types); matching clusters only against matching. (A matching question
     tests 4–5 facts, so it isn't a dup of a single-fact question — this
     avoids over-merging and means slightly less ANN work than a global
     per-topic comparison.)
  3. Assign `concept_cluster_id`: inherit the matched question's cluster
     if above threshold (within its comparison group), else mint a new
     one.
- **Within-batch:** when a background overgeneration batch is processed,
  each new question is clustered against both the persisted bank and the
  others in its own batch, so two near-identical new questions get the
  same cluster id (and thus can never co-occur in a quiz).
- **At serving:** cross-similarity is enforced entirely by Step B's
  `DISTINCT ON (concept_cluster_id)` — no embeddings compared, no loop, no
  possibility of triggering generation.
- **Threshold:** start conservative; tune from manual review once real
  output exists. Transitive clustering can occasionally over-merge — a
  known, accepted best-effort limitation (SPEC section 7).

## 6. Constraints carried over from SPEC.md

- Exact requested question count must always be delivered (sections 2, 4).
- Coverage rule (10a) and accuracy-weighted distribution (10a) must be
  applied before bank lookup/generation, not after — they determine *what*
  to look up/generate, not a post-hoc filter.
- Matching questions need source content with enough pairable facts —
  when a topic can't reasonably support the requested matching-question
  count (too few pairable facts, or the user has seen all available
  matching questions for that scope), the shortfall is filled with
  MCQ/fill-in-blank instead (resolved, section 8). The requested total
  count is always still met; only the type mix shifts.
- Every question, bank-sourced or freshly generated, must retain a
  reference to its source `chunk_id` for the solutions screen (SPEC 8a).

## 7. Task breakdown (small, independently reviewable tasks)

1. **Implement Step A (scope resolution)** in isolation — given a mock
   request, output the allocation list, tested against the coverage rule
   and equal-weighting/round-robin logic with no accuracy data yet.
2. **Add accuracy-weighted adjustment to Step A** — using mocked
   performance-history data, confirm weak topics get weighted correctly
   and topics below the minimum sample size fall back to random.
3. **Implement bank lookup (Step B)** against a small manually-seeded
   test bank — confirm per-user exclusion (via `seen_questions`), the
   `DISTINCT ON (concept_cluster_id)` filter, and shortfall counting work
   in a single query.
4. **Build the MCQ prompt template and schema**, test generation +
   validation on one sample chunk from Section 1's output.
5. **Build the fill-in-blank prompt template and schema**, same test
   pattern.
6. **Build the matching prompt template and schema**, tested specifically
   on a chunk tagged `has_table: true` (section 6/Section 1 output) to
   confirm it produces well-formed pairs from structured source content.
7. **Implement reliability handling (section 4)** — retries, fallback
   provider, schema-validation retry — tested by deliberately forcing
   failures (e.g., a bad API key) to confirm fallback actually triggers.
8. **Implement exact-hash duplicate detection** at intake against a
   seeded bank with a known-duplicate test case.
9. **Implement write-time embedding clustering (`concept_cluster_id`
   assignment)** using pgvector ANN in the same topic+difficulty, within
   the restricted comparison group (MCQ+fill-in-blank together; matching
   separate) — test that a reworded MCQ↔fill-in-blank near-duplicate
   shares a cluster, a matching question with an overlapping fact does
   NOT share a cluster with an MCQ, and an unrelated question gets a new
   cluster.
10. **Implement within-batch clustering** for a background overgeneration
    batch — two near-identical new questions in one batch get the same
    cluster id.
10a. **Implement background overgeneration** (section 2, Step C) — confirm
    the user's quiz is served first, and extra questions are generated,
    validated, clustered, and banked in a background task that does not
    delay the response. Confirm a background failure doesn't affect the
    served quiz.
10b. **Implement parallel generation across allocation lines** — a request
    needing generation for several topics at once fires those LLM calls
    concurrently; confirm total latency is close to one call, not the sum.
11. **Wire Steps A–D together end-to-end** for one topic, one difficulty,
    one question type, confirmed against a test user with partial prior
    history (some seen questions to exclude).
11a. **Verify cross-type similarity scope at query level** — seed the bank
    with an MCQ and a fill-in-blank testing the same fact sharing a
    `concept_cluster_id`; confirm one quiz assembly never returns both.
    Also confirm a matching question testing an overlapping fact is NOT
    excluded (different comparison group), all via Step B's `DISTINCT ON`
    with no runtime embedding comparison.
12. **Implement scoring, performance-history updates, and the four-option
    post-quiz results screen** (Step E) — go home, new quiz (same topic,
    unseen questions), requiz (replay, no history update), see solutions.
    Confirm the requiz path does NOT write to performance history.
13. **Run a full end-to-end test**: one complete quiz request through
    delivery, submission, scoring, and history update, on real data from
    Section 1's chunking output.
14. **Latency verification (SPEC latency target):** measure end-to-end
    time for the worst realistic case — a large question count on a
    cold-bank topic requiring full generation — and confirm it lands in
    the 10–20s range with parallel generation. If it doesn't, this is an
    Iterate trigger (revisit the spec's latency assumptions), not a
    code-level workaround.

## 8. Resolved decisions (previously open)

- **Matching-question fallback (resolved):** when a topic can't supply the
  requested matching questions — either it lacks enough pairable content,
  or the user has already seen all possible matching questions for that
  scope — the shortfall is filled with **MCQ/fill-in-blank questions
  instead**, rather than failing or telling the user. The requested count
  is still met; only the type mix shifts.
- **Duplicate-similarity threshold (verified against real data):**
  **cosine similarity ≥ 0.90** to treat two questions as the same concept.
  This value was calibrated empirically (not guessed): the original 0.85
  pre-data estimate proved slightly too permissive (3 false merges on the
  test dataset), while 0.90 gives ~0 false merges while still catching real
  near-duplicates — including a low-word-overlap pair (0.910) that a text/
  hash match would miss, confirming the embedding layer earns its place.
  Stored as a config value (not hardcoded). **Caveat:** calibrated on one
  subject/class dataset; subjects with more formulaic phrasing (e.g. Math)
  may behave differently, so re-check the threshold if odd merges or missed
  duplicates appear in a very different subject.
- **LLM providers (resolved):** **primary = OpenAI** (credits purchased,
  autopay enabled, so transient issues self-recover). **Secondary =
  Anthropic** (fallback, to be added when funded). Sequential fallback per
  SPEC section 9.
  - **Budget caution:** on a small initial credit budget, overgeneration
    (which deliberately spends more upfront to pre-fill the bank) should
    start with a **small factor or be disabled** until the core flow is
    tested, then dialed up on a larger budget. The overgeneration amount
    must be a config value, not a fixed constant, for this reason.

---

# PLAN.md — Section 3: Web Application (Auth, App Flow, Grading, Results, Dashboard)

Scope: everything that turns the built RAG + generation engine (Sections 1–2)
into a usable website — authentication and profiles, the quiz-selection flow,
quiz-taking UI, grading, the results/solutions screens, and the performance
dashboard (SPEC.md sections 3, 4, 5, 8, 8a, 11, 11a). Reference-corpus capture
(feeding solution formatting) is included where it touches grading. No
implementation code — Implement step follows review.

## 1. Goal (recap, in plan terms)

A logged-in student can go from the main page through the full selection
flow to a delivered quiz, take it (including drag-and-drop matching), submit,
see a score with four next-step options, review solutions, and view a
performance dashboard — all backed by the Section 1–2 engine.

## 2. Tech stack (with reasoning from current best practice)

| Layer | Choice | Why |
|---|---|---|
| Backend framework | **FastAPI (Python)** | Matches the Section 1–2 Python engine; async support helps the parallel-generation requirement (Section 2); clean typed request/response models via Pydantic (already used for question schemas). |
| Frontend | **React** | Largest ecosystem for the interactive pieces this app needs (drag-and-drop matching, dashboard charts); component model fits the repeated quiz-question UI. |
| Auth approach | **Managed auth provider — Clerk** (resolved, section 8), with the **frontend holding Clerk's session and attaching it as a Bearer token to every API call**, verified by FastAPI against Clerk's JWKS | 2026 consensus for first-party web apps: don't hand-roll auth — still true here. The original "HttpOnly cookie" framing assumed a same-origin, server-rendered session; this app is a separate SPA (Vite, its own origin) calling a separate API (FastAPI, its own origin), which is a cross-origin architecture — the standard, Clerk-documented pattern there is `getToken()` on the frontend + Bearer-token JWT verification on the backend, not a shared cookie (a cross-origin cookie needs `SameSite=None`, which reintroduces CSRF exposure a same-origin cookie was chosen to avoid, for no benefit once the token is already short-lived and never touches `localStorage` — Clerk's SDK keeps it in memory). Risk profile is unchanged from the original reasoning: no long-lived credential in `localStorage`, short-lived tokens, provider-managed refresh. |
| Social login | **OAuth 2.0 + OIDC via the managed provider** | "Sign in with Google" handled by the provider; never touch third-party credentials directly. |
| Password reset / email verification | **Handled by the managed provider** | Removes the need for a separate transactional email service (resolves the earlier email-provider open question). |
| Database | **PostgreSQL + pgvector** (already chosen, Section 2) | One database for app data, engine data, and embeddings. |
| Background tasks | FastAPI background tasks / a task queue | For non-blocking overgeneration (Section 2) and any async work off the request path. |

**Auth decision (locked): managed provider.** The most error-prone auth
work (password reset, email verification, OAuth edge cases, ongoing
security) is delegated to a managed provider rather than hand-rolled.
Provider choice narrowed to Supabase Auth (natural if Postgres/Supabase
hosts data) or Clerk (excellent DX, provider-agnostic); final pick can be
made at task 2 based on the hosting choice, but "hand-roll it all" is off
the table.

## 3. Backend API surface (endpoints, grouped)

Auth (mostly provided by the chosen library, listed for completeness):
- sign up (email/username + password), log in, log out, session check,
  password reset, OAuth callback.

Profile:
- get/update profile (stores default class — SPEC section 4).

Quiz selection metadata (drives the flow; reads Section 1 data):
- list subjects for a class; list chapters for a class+subject (real names,
  SPEC section 5); list topics for a chapter; for an "all topics"/"all
  chapters" selection, return the sub-unit count so the frontend can offer
  only valid question-count options (coverage rule, SPEC 10a).

Quiz lifecycle (drives Section 2 engine):
- create quiz (payload = resolved scope + difficulty + count + types) →
  returns the assembled quiz; records delivered questions as "seen".
- submit quiz (payload = user answers) → grades, records attempt + updates
  performance counters, returns score.
- get solutions for a submitted quiz → per-question correctness + grounded
  explanations (SPEC 8a).
- requiz (replay same question set) → returns the same quiz, flagged so
  submit does NOT record to history (SPEC section 11).

Performance:
- get dashboard data (aggregated accuracy by subject/chapter/difficulty/
  type + attempts over time — derived from counters, SPEC 11/11a).

## 4. Grading engine (SPEC section 8)

- **MCQ / multi-part correctness:** exact-match against stored correct
  option(s); deterministic, no LLM.
- **Matching:** drag-and-drop answer compared to correct pairing; **1 mark
  only if all pairs correct** (SPEC section 8); per-row green/red feedback
  data returned for the solutions screen.
- **Fill-in-blank numeric:** exact match after normalizing formatting.
- **Fill-in-blank text (updated 2026-10-29):** Unicode/whitespace
  normalization -> explicit canonical/accepted-answer checks -> reviewed
  chemical equivalence -> ordinary-text embedding fallback (0.75, using
  the same MiniLM model through the ONNX runtime). Chemistry is resolved
  deterministically before text matching; preserve CO/Co distinctions and
  accept lowercase notation only when unambiguous in the registry.
  Never use embeddings to approve a different formula. No LLM grading call.
  Numeric answers keep their existing exact rules and bypass alias-based
  overrides of the numeric value.
- Every question carries its `source_chunk_id`, so the solutions screen can
  show an explanation grounded in real textbook content; where a captured
  reference-corpus answer exists (SPEC section 6), it styles the
  explanation format.

## 5. Frontend screens

- **Auth screens:** sign up, log in, social login button, password reset.
- **Main page:** "start a quiz" entry + link to performance dashboard.
- **Selection flow:** class (prefilled from profile, editable) → category →
  subject → chapter(s)/topic(s) with "all chapters"/"all topics" shortcuts
  → difficulty → question count (only valid options shown) → question
  types. Flow skips steps made redundant by "all" shortcuts (SPEC 5/14).
- **Quiz-taking screen:** renders each question by type; matching is
  drag-and-drop (left list fixed, right blocks draggable — SPEC section 8).
- **Results screen:** score + four options — go home / new quiz (same topic,
  unseen) / requiz (same questions, not recorded) / see solutions.
- **Solutions screen:** read-only quiz reopened, green/red per question
  (and per matching row), explanation below each (SPEC 8a).
- **Performance dashboard:** accuracy by subject/chapter/difficulty/type +
  attempts over time.

## 6. Constraints carried over from SPEC.md

- Latency target (10–20s) applies to the create-quiz endpoint — it calls the
  Section 2 engine, so parallel generation + non-blocking overgeneration
  must be preserved end-to-end through the API.
- Chapters/topics shown by real name everywhere (SPEC section 5).
- Requiz path must bypass performance-history writes (SPEC section 11).
- Secrets (DB URL, OpenAI key, session/auth secret) via environment
  variables only — never committed (also a deployment requirement).
- Green/red per-item feedback convention applies across all multi-part
  question types (SPEC section 8).

## 7. Task breakdown (small, independently reviewable tasks)

1. **Scaffold backend (FastAPI) + frontend (React)** projects wired to the
   existing database; health-check endpoint proves end-to-end connectivity.
2. **Integrate the chosen auth solution** — sign up, log in, log out,
   session cookie set correctly (HttpOnly/Secure/SameSite). Test a
   protected endpoint rejects unauthenticated requests.
3. **Add social login (OAuth/OIDC)** — one provider (Google) end-to-end.
4. **Confirm password reset + email verification** work via the managed
   provider (these are provider-handled, not custom-built — mainly a
   configuration + test task, not an implementation one).
5. **Profile endpoints + default-class storage**, read on the selection
   flow's class step.
6. **Selection-metadata endpoints** (subjects/chapters/topics/sub-unit
   counts) reading Section 1 data; verify real names and coverage-count.
7. **Build the selection-flow UI** with the "all" shortcuts and
   valid-only question-count options; no quiz generation yet, just the flow.
8. **Wire create-quiz endpoint to the Section 2 engine**; confirm a full
   assembled quiz returns and delivered questions are marked seen.
9. **Build the quiz-taking UI** for MCQ and fill-in-blank first.
10. **Build the drag-and-drop matching UI** (left fixed, right draggable).
11. **Implement the grading engine** (section 4), including synonym-tolerant
    fill-in-blank; unit-test each type.
12. **Build the results screen** with the four options; wire requiz to
    bypass history.
13. **Build the solutions screen** (green/red + grounded explanations).
14. **Implement performance-dashboard endpoint + UI** (aggregations from
    counters).
15. **End-to-end pass:** sign up → full selection → quiz → submit → score →
    solutions → dashboard reflects the attempt, on real data.
16. **Latency check on create-quiz** through the full API (ties to Section 2
    task 14) — worst realistic case within 10–20s.

## 8. Open questions to resolve before implementing

- **Managed auth provider — final pick (resolved): Clerk.** Chosen over
  Supabase Auth because the app's Postgres stays local/self-hosted
  (already running with pgvector for Sections 1–2) rather than migrating
  to Supabase-hosted Postgres — Clerk is provider-agnostic, so it plugs in
  without moving the database. FastAPI verifies Clerk session JWTs (via
  Clerk's JWKS endpoint) on protected routes; the React frontend uses
  `@clerk/clerk-react` for sign-up/login/session/Google OAuth UI. The
  `user_id` used throughout Section 2's tables (`seen_questions`,
  `performance_counters`, `quiz_attempts`) is Clerk's user id (a string),
  matching the existing `TEXT` columns — no schema change needed. A new
  `profiles` table stores the one piece of app-specific per-user state
  SPEC section 4 needs beyond what Clerk holds: `default_class`.
- **Dashboard visuals (resolved):** the design pass (`Quiz Master - Final
  Screens.dc.html`, screen 11 "My progress") settled this — accuracy by
  subject, by chapter within a subject, by question type, and attempts
  over time, all derived from `performance_counters` and `quiz_attempts`
  per SPEC 11/11a. No further open decision here.

*(Resolved: auth is a managed provider, not hand-rolled — section 2.
Password reset / email verification are handled by that provider, so no
separate transactional email service is needed.)*

## 9. Design reference

Final screen designs (colors, type, layout, copy) are in
`Design scope and variations.zip` → `Quiz Master - Final Screens.dc.html`
— 14 screens covering the full v1 flow: log in, create account, home
(subject board + weak-topic nudge + daily-limit counter), build-a-quiz
(6-step wizard), the generation-wait screen, MCQ/fill-in-blank/matching
question screens, results (score + four options), solutions, my progress
(dashboard), profile & settings, generation-failed (retry / smaller quiz
from bank), and daily-limit-reached. `NCERT Quiz - Design Explorations.dc.html`
holds earlier variants and is not the build target. Frontend implementation
should match the final-screens file's design tokens (Instrument Serif +
Public Sans + IBM Plex Mono; warm off-white/terracotta/sage palette) and
layout, not redesign from scratch.

## 10. Known engine gap to close during Section 3 wiring

`quiz_engine.assemble_quiz` (Section 2, task 11) is documented as scoped
to a single chapter per call — it doesn't yet accept sub-units spanning
several chapters, which SPEC section 5 step 6b/6c allows (a Category B
quiz can select multiple specific chapters at once).

**Resolved approach: one flat `resolve_scope` call across every topic in
every selected chapter, not a nested chapter-then-topic split.** SPEC
10a's coverage rule, equal-weight/round-robin remainder, accuracy-weighted
selection, and 25+-sub-unit overflow are all defined over a single flat
pool of sub-units — nesting a chapter-level distribution on top of the
topic-level one would evaluate the coverage rule and accuracy-weighting
twice, approximately, instead of once, correctly. So Section 3's
quiz-creation service:
1. Resolves the full sub-unit list (every topic across every selected
   chapter — or every topic in the whole subject, for "all chapters") and
   a `topic -> chapter` map (Section 1 catalog data, section 3 above).
2. Calls `scope_resolution.resolve_scope` **once** over that flat topic
   list — reusing Section 2's tested selection/distribution/coverage-rule
   logic exactly as-is, with no new engine code.
3. For each returned `AllocationLine`, calls `bank_lookup.lookup_bank` and
   the generation `generate_fn` directly (the same calls
   `quiz_engine.assemble_quiz` makes internally), using the line's own
   chapter from the `topic -> chapter` map — this is the one piece
   `assemble_quiz` can't do itself (it takes a single `chapter` for the
   whole call), so Section 3 inlines that loop rather than changing
   Section 2's signature.
4. Marks delivered questions seen (`quiz_engine.mark_seen`, reused as-is).

This keeps Section 2's engine and its existing 94 passing tests untouched
— `resolve_scope`, `lookup_bank`, `generate_fn`, and `mark_seen` are all
reused verbatim; only the orchestration loop that ties them together for
a multi-chapter request is new, in Section 3's `quiz_service.py`.

## 11. Implementation status (2026-08-31)

**Backend — done, 130/130 tests passing** (94 Section 1–2 + 36 new):
tasks 1, 2, 5, 6, 8, 11, 12, 13, 14 from section 7's task list. New
modules: `auth.py` (Clerk JWT verification), `catalog.py` (Section 1
Chroma metadata → subjects/chapters/topics/coverage counts),
`quiz_service.py` (multi-chapter wiring per section 10, daily-limit
tracking, submit/solutions orchestration), `explanations.py` (lazy
LLM-generated, cached solutions-screen explanations — SPEC 8a, not built
in Section 2), `performance_service.py` (dashboard aggregation),
`api_schemas.py`, `deps.py`, `main.py` + `routers/`. Schema additions:
`profiles`, `quiz_generation_log` tables, `questions.explanation` column.

**Frontend — scaffolded and built, all 14 design screens implemented**
(tasks 1, 3–4 by configuration, 7, 9, 10, 12, 13, 14): Vite + React +
TypeScript, `@clerk/clerk-react` for auth UI, `@dnd-kit` for the matching
drag-and-drop, `recharts` for the dashboard, `@tanstack/react-query` for
data fetching. `tsc -b` and `vite build` both pass clean; the app boots
in a real browser with zero console errors (verified via Playwright,
screenshot of the pre-auth state).

**Auth wired to a real Clerk app (2026-08-31, via `clerk` CLI):** linked
to app `app_3IeJvrvhC1bYAjAhuumta7Xj3xl` ("Quiz Master"). Migrated from
`@clerk/clerk-react` (v5) to `@clerk/react` (v6, what `clerk init`
installs) — `SignedIn`/`SignedOut` replaced by the unified
`<Show when="signed-in"|"signed-out">`; added a `<UserButton/>` to
`AppShell`. `frontend/.env`'s `VITE_CLERK_PUBLISHABLE_KEY` and
`backend/.env`'s `CLERK_ISSUER` are both populated with real values
(`CLERK_ISSUER` derived from the publishable key, then confirmed by
fetching its live JWKS endpoint directly). Google is already an enabled
first-factor on this Clerk instance (SPEC section 4) — no extra
dashboard step needed.

**Task 15 (end-to-end pass) — done, verified live, not just by unit
test.** Real browser automation (Playwright) can't get past Clerk's
Cloudflare Turnstile bot check on the hosted sign-up form — expected and
correct, since that check exists specifically to stop headless
automation. Verified instead via Clerk's Backend API: created a real
test user (`clerk users create`), signed them in via an impersonation
ticket (`clerk impersonate --print`), then drove the actual app with
that real session cookie against the real running backend (no mocks).
Confirmed working end-to-end: Home (real subjects, per-subject accuracy,
daily-usage widget) → 6-step build-a-quiz wizard → a genuinely
LLM-generated, textbook-grounded MCQ question rendered correctly →
answering + pill navigation → submit → Results screen (score ring +
exact four options) → Solutions screen (green/red marking + a real
LLM-generated grounded explanation, SPEC 8a) → Progress dashboard
(overall/by-subject/by-chapter/by-type/attempts-per-week, reflecting the
real attempt just recorded) → Settings (real profile, default-class
write-then-prefill round trip, daily-usage bar). Zero browser console
errors at any step. Test user deleted afterward; temporary Playwright
tooling removed from the repo.

**Task 16 (latency check) not yet done** — the verification run above
used a single 5-question MCQ quiz, which isn't the worst-case scenario
Section 2's task 14 already measured (large count, cold-bank, multiple
topics in parallel). Worth a dedicated pass before considering Section 3
fully closed out, but not a blocker for normal use.

## 12. Accepted fill-in-the-blank answers (2026-10-29)


Trigger: a live student answered `mgo` for a question keyed to magnesium
oxide and was incorrectly rejected by the embedding-only fallback.

Implementation:
1. Add optional `accepted_answers` to FillInBlankPayload (default empty
   for backward compatibility), validate bounds/short terms/no algebraic
   expressions, and ask the generation prompt for source-supported aliases.
   Store the resulting list automatically in `questions.payload` JSONB.
2. Add `answer_aliases.py` with a small, reviewed registry of chemical
   names/formulas. For covered substances, the registry determines accepted
   equivalents rather than trusting an LLM-proposed chemical alias. Preserve
   meaningful case, normalize subscripts, reject ambiguous lowercase forms.
   This registry is intentionally incomplete; expand only with reviewed facts.
3. Grade canonical/accepted answers before embeddings. Registry chemicals
   and formula-shaped answers cannot fall through to fuzzy semantic grading.
   Ordinary text retains the existing threshold and numeric behavior stays
   exact; no extra paid grading request is introduced.
4. Derive reviewed equivalents for legacy payloads at runtime; no destructive
   migration or bulk model call is needed. Past attempts/counters remain
   immutable. New attempts, including review replays, use the current rules.
5. Return effective accepted alternatives only in the solutions payload,
   and display them alongside the canonical answer. The delivered quiz
   continues stripping all answer information.

Verification: cover MgO/mgo/magnesium oxide, Unicode subscripts, CO versus
Co (and rejection of ambiguous `co`), wrong formulas even with an overly
permissive embedding stub, numeric alias bypass attempts, invalid generated
aliases, old payload compatibility, and answer-field stripping. Run the
backend grading/prompt regressions plus frontend build/lint before deployment.
Generated ordinary-text aliases remain best-effort source-grounded data;
schema validation alone is not a factual equivalence guarantee.


## 13. Matching generation and latency implementation — 2026-10-29

- Replace matching stem-only hashes with versioned canonical mapping hashes.
  Preserve formula case, ignore item order, and compare legacy payloads before
  intake. Embed the actual mapping to avoid generic-stem clustering.
- Bulk-read solution rows and cached explanations; parallelize missing provider
  calls with LLM_MAX_WORKERS (default 6), then persist on the caller thread.
  Reuse source/style retrieval within a batch and cache immutable source chunks.
- Bound existing generation concurrency with the same setting. Cache subject,
  chapter and topic metadata; process restart invalidates corpus caches.
- Verify legacy duplicate compatibility, different mappings with identical
  instructions, formula case, cache-only requests, failure atomicity, and
  bounded concurrency at 5/15/25 questions. Run existing grading/generation
  regressions, deploy, and verify matching creation through the live app.
- Remaining work: cross-allocation scheduling, database pooling, durable
  pre-generation, scientific alias/content review and detailed server timings.
  These are not claimed as implemented by this change.

