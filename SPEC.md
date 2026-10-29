# SPEC.md — NCERT Quiz Generator

## 1. Problem

Students in grades 6–12 preparing for school exams need practice quizzes that are
tightly grounded in their actual NCERT textbook content — not generic internet
questions that may not match their syllabus, terminology, or difficulty level.

Students currently rely on manually written question banks or unverified online
quizzes, which are:
- Slow to produce
- Inconsistent in difficulty and coverage across a chapter
- Not grounded in the exact textbook wording/facts students are tested on
- Limited in question variety (usually just MCQs, missing formats used in real exams)

## 2. Who this is for

**Primary user: Students (grades 6–12)** who want to self-test on a specific
topic, chapter, or whole subject, at a difficulty level they choose, and get
immediate, explained feedback.

This version is a single-user-type product — every account is a student
account. There is no teacher role: no rosters, no assigning quizzes to other
users, no viewing other users' results. Confirmed out of scope for v1 (see
section 13) — a separate feature to revisit once the core product works.

## 3. Product form

A **website** with user accounts. A user creates a profile, logs in, and
lands on a main page from which they start a quiz.

## 4. Authentication

- Users can sign in either via email/password (with a username), **or** via
  a third-party sign-in option (e.g., "sign in with Google" or another major
  email provider) — matching the familiar pattern used by most consumer
  websites today.
- A profile stores a **default class** for the user, to speed up repeat use.

## 5. User flow

1. **Sign up / log in.**
2. **Main page.** The user selects "start a quiz."
3. **Select class** (6 through 12) — pre-filled with the user's default
   class from their profile, but always editable/re-askable, since a
   student may want to practice content above their current grade.
4. **Select quiz category** — exactly one of:
   - **Category A: Subject-wide quiz** — drawn from an entire subject for
     the selected class, not scoped to one chapter.
   - **Category B: Chapter/topic quiz** — scoped to specific chapter(s),
     and optionally specific topic(s) within them.
5. **If Category A:** select the subject. (No chapter/topic prompts.)
6. **If Category B:**
   a. Select the subject.
   b. Select chapter(s) — **or** choose "all chapters" (skips straight to
      step 7; equivalent to a subject-wide quiz for that subject).
   c. If specific chapter(s) were chosen: select topic(s) within the
      chapter — **or** choose "all topics" (whole chapter, no further
      topic-level prompt).

   **Chapters and topics are always shown and referred to by their actual
   textbook names** (e.g., "Photosynthesis in Higher Plants," "Site of
   Photosynthesis") — never as generic "Chapter 1 / Chapter 2" or "Topic 1
   / Topic 2" labels. Any internal chapter/topic numbering used for
   ordering or coverage counting (section 10a) is implementation detail
   only, never surfaced in the UI or used as the way content is identified.
7. **Select difficulty:** easy, medium, or hard (single choice per quiz;
   not mixed within one quiz in this version).
8. **Select number of questions:** a fixed choice of **5, 10, 15, 20, or 25**
   (not a free-form number).
9. **Select question type(s):** MCQ, fill-in-the-blank, and/or matching.
   Assertion-reason (both forms) and multi-correct-choice are confirmed
   **out of scope for v1** — planned for a later version.
10. Application assembles the quiz (from the question bank and/or fresh
    generation — see section 7) and the user attempts it.
11. User submits answers. The quiz is graded immediately, and the attempt
    (with its per-question results) is recorded to performance history
    (section 11). On the same results page, the user is shown their
    **overall score** (X correct out of Y) and **four options**:
    - **Go home** — return to the main page.
    - **Start a new quiz on the same topic** — same scope, but freshly
      assembled with questions the user has not seen before (section 7).
    - **Requiz with the same questions** — replay the identical question
      set just taken. This is treated as a **retake for review**: its
      score is **not recorded to performance history** and does **not**
      affect accuracy or weak-topic weighting (section 11a), since the
      user has effectively already seen these questions and their answers.
    - **See solutions** — open the solutions review screen (section 8a).
12. **Retakes:** a student can retake the exact same
    class/subject/chapter/topic/difficulty combination as many times as
    they want. "Start a new quiz on the same topic" serves **questions the
    user has not personally seen before** wherever possible (section 7),
    and each such attempt is logged separately in history. "Requiz with
    the same questions" replays the identical set and is not logged to
    performance history (see step 11).

## 6. Content grounding (RAG)

- NCERT textbook content is broken into chunks **per topic**, following the
  topic structure already present in each chapter's index/table of contents
  — not arbitrary fixed-size text chunks.
- A "chapter" is a collection of topic-level chunks. Category B's topic
  selection maps directly onto this structure: one topic → that topic's
  chunk(s); "all topics" → every chunk under that chapter; a subject-wide
  quiz → every chapter's chunks for that subject and class.
- **Topics vs. subtopics (mandatory rule, 2026-09-02):** only real
  chapter-level topics are ever stored, surfaced in the topic picker, or
  used as a `topic` value anywhere. A topic small enough to fit one normal
  chunk is stored as a single chunk; a topic too large for one chunk is
  split at its own internal subtopic boundaries for chunking purposes only
  — every resulting chunk keeps the SAME parent topic's name/number, just a
  different `chunk_index`, so retrieval and the app's topic list always see
  one topic, never a subtopic promoted to sibling-topic status. (Found in
  production: a large topic's subtopics were briefly showing up as their
  own entries in the chapter's topic list — this rule exists specifically
  to prevent that regressing.)
- **Introduction (mandatory rule, 2026-09-02):** any real chapter content
  that appears before the first detected topic heading is stored as a topic
  literally named "Introduction" — never a placeholder/bracketed label.
  (Some chapters print their own "Introduction" heading as their actual
  first topic — that's just a normal topic in that case, this rule only
  covers chapters with no such heading where real content still precedes
  topic 1.)
- **No single universal extraction heuristic (mandatory rule, 2026-09-02):**
  different NCERT textbooks use genuinely different page-layout/heading
  conventions (confirmed: numbered "N.M Heading" style in Maths/Science;
  ALL-CAPS unnumbered subheadings in the older Social Science production
  era; Title-Case font-size/color-tiered headings in the revised-curriculum
  Social Science books) — topic/heading detection is verified, and given
  book-specific logic where needed, **per textbook**, never assumed to
  generalize from one book's design to another's untested.
- **Non-factual callout/activity content is excluded from topic grounding
  text**, verified per textbook: e.g. the revised-curriculum Social Science
  books' "LET'S EXPLORE" (hands-on activity prompts) and "THINK ABOUT IT"
  (reflective/open-ended questions) boxes carry no gradable facts and are
  dropped; a book's genuinely factual supplementary box (that same series'
  "DON'T MISS OUT" box) stays in the enclosing topic's grounding text. An
  end-of-chapter recap that restates facts spanning multiple topics in one
  summary (that series' "Before we move on ...") is also dropped rather
  than misattributed to whichever topic happens to be last.
- **Question-wording style reference for generation (2026-09-02):** some
  books' in-text exercise questions ("Questions, activities and projects"
  in the revised-curriculum Social Science books) have no printed answer,
  so they don't serve section 8a's solutions-style-reference purpose, but
  their wording is real NCERT-authored, grade-level-calibrated question
  phrasing — valuable for keeping *generated* questions at an
  age-appropriate reading level. These are stored in the same
  `reference_corpus` table (`topic` NULL — chapter-level, not
  topic-specific; `answer` NULL; `source = 'ncert_activity'`) and fed into
  the generation prompt as a wording-style example (not a fact source —
  the model must not copy its content, only its grammar/complexity level).
  Excluded from section 8a's existing solutions-style-reference query,
  which needs a real question+answer pair.
- Every generated question must be traceable to the specific chunk(s) it was
  generated from, so grading explanations can reference real textbook content.
- **Forward-looking data capture (not used in v1 generation, but captured
  now to avoid re-processing later):**
  - Images/diagrams encountered during parsing are **not discarded** —
    the image and its caption are stored and linked to the topic/chunk
    they belong to, even though v1 does not generate diagram-based
    questions (see section 13). This is pure storage now; using it for
    generation is deferred.
  - In-text exercise questions and previous CBSE question papers are
    **not used as fact-grounding content** (kept out of topic chunks, per
    the parsing rules), but are captured and stored separately, tagged by
    class/subject/chapter/topic, as a future reference corpus for
    exam-style question generation (see section 13). **The official
    answer/solution text, as printed in the source PDF, is captured and
    stored alongside each question** — not just the question itself. Not
    used for question generation in v1, but the stored answer format is
    used starting in v1 as a style reference for the solutions feature
    (see section 8a).

## 7. Question bank and reuse strategy

- Every validated question the app generates (LLM-produced and passed
  validation) is stored in a bank, tagged by class, subject, chapter, topic,
  difficulty, and question type.
- **On every quiz request:** first pull as many eligible questions as
  possible from the bank for the requested scope, **excluding any question
  this specific user has already been served**. Only call the LLM to
  generate the remaining shortfall needed to hit the requested question count.
- **Tracking "seen":** every question delivered to a user in an assembled
  quiz is recorded in a `(user, question)` seen-record at delivery time —
  this is the record bank lookup excludes against, not just answered
  questions but anything actually shown.
- Different users can be served the same bank questions — reuse across
  users is expected and fine. Only a single user's own history is excluded.
- No fixed threshold (like "10 quizzes") is used to decide when to check the
  bank — the bank is checked first on every request, by design, so bank
  usage naturally increases over time as it fills up.
- **Overgeneration to reduce future LLM calls:** when generation is needed
  to fill a shortfall, the app may request more questions than immediately
  needed (e.g., request roughly double the shortfall, up to a cap) so the
  bank fills up faster for that scope and future requests are more likely
  to be served from the bank alone. All overgenerated questions still go
  through the full validation and duplicate-check pipeline before storage
  — "extra" does not mean "unvalidated."
- **Duplicate handling when adding new LLM output to the bank:**
  1. Exact/near-exact text match against existing bank entries in the same
     scope → discard, don't store.
  2. Semantic similarity check (embedding comparison) against existing bank
     entries in the same scope → if above a similarity threshold, treat as
     a duplicate and discard rather than storing a reworded repeat.
  3. If a generated question is discarded as a duplicate and more questions
     are still needed to fill the quiz, generate a replacement (same retry
     mechanism as section 9's malformed-output handling).
  - **Known limitation:** semantic similarity thresholds are best-effort,
    not a guarantee of zero duplicates. Accepted as a v1 limitation.
  - **Duplicate/similarity handling is precomputed at write time, not at
    serving time (architecture decision).** Real-world dedup systems
    (search engines, image/video near-duplicate detection, e-commerce)
    move similarity work off the serving path: clusters are computed at
    intake and stored, and serving just filters by a stored cluster id.
    This app follows that pattern. When a validated question enters the
    bank (already a background, off-request-path operation):
    1. A normalized exact hash (e.g. SHA-256 of cleaned text) is computed
       — catches literal duplicates deterministically, no vector needed.
    2. Its embedding is compared (approximate nearest-neighbor) against
       existing bank questions in the same topic + difficulty, within a
       **restricted comparison group**: MCQ and fill-in-blank are
       clustered together (both are single-fact question types), while
       **matching questions are clustered only against other matching
       questions**. Rationale: a matching question with 4–5 pairs tests
       several distinct facts at once, so it is not a duplicate of a
       single-fact MCQ/fill-in-blank even when one pair overlaps — treating
       them as interchangeable would over-trigger dedup and drop good
       matching questions. Consequence (accepted deliberately): a matching
       question and an MCQ testing an identical fact may both appear in one
       quiz.
    3. It is assigned a `concept_cluster_id`: inherit an existing
       question's cluster if above the similarity threshold (within its
       comparison group), else mint a new one. This single id covers
       same-type and (for MCQ/fill-in-blank) cross-type near-duplicates,
       replacing the earlier separate `similarity_group` and read-time
       cross-type check.
  - **At quiz assembly (serving), cross-similarity is a pure relational
    filter:** never select two questions sharing a `concept_cluster_id`
    (a `DISTINCT ON (concept_cluster_id)` query over an indexed column).
    No embeddings are compared at serving time, no over-fetch loop, and it
    can never trigger generation — the database returns at most one
    question per concept. This keeps the latency target safe.
  - **Known limitation:** write-time clustering uses transitive grouping
    (if A~B and B~C, all three share a cluster even if A is not directly
    similar to C), which can occasionally over-merge distinct questions.
    Threshold is tuned empirically; best-effort, not a guarantee — same
    framing as other checks. A format-independent "concept key" derived
    directly from question meaning is a possible future refinement.

## 8. Answer grading approach

- **MCQ:** exact match against the correct option.
- **Fill-in-the-blank:**
  - Numeric answers: exact match after normalizing formatting (e.g., strip
    commas/units). No tolerance range in v1.
  - Text answers (updated 2026-10-29): normalize Unicode and whitespace,
    then check the canonical answer and reviewed term/formula equivalents.
    Unreviewed model aliases and semantic similarity cannot authorize credit.
    Store alternatives in the existing question JSON payload. Generation
    must propose only source-supported equivalents, not related concepts;
    rewrite questions with ambiguous blanks. Alternatives must be short,
    nonempty terms (at most 8 alternatives, each at most 80 characters),
    subject to the same term/algebraic-expression validation as answers.
  - Chemical equivalence uses a reviewed name/formula registry, not fuzzy
    embedding similarity. For example, magnesium oxide accepts `MgO` and
    unambiguous lowercase input `mgo`. Normalize Unicode subscripts
    (`H₂O` -> `H2O`) while preserving meaningful chemical capitalization:
    `CO` (carbon monoxide) and `Co` (cobalt) must not match each other;
    ambiguous lowercase `co` is rejected. Do not guess unknown compounds.
    For registry-covered chemicals, model-proposed alternatives cannot
    override the reviewed equivalence set. Other formula-shaped answers
    require explicit exact alternatives; embeddings cannot approve a
    different formula. Numeric answers retain their existing exact rules,
    and alternatives cannot authorize a different number.
  - Legacy bank questions without `accepted_answers` remain valid. Apply
    reviewed chemical equivalences at grading/review time so old magnesium
    oxide questions benefit on future attempts without a database migration.
    Do not regrade previously saved attempts or change performance history.
    The solutions screen shows the canonical answer and current accepted
    alternatives, including when the student used a correct alternative;
    it explains that saved scores remain unchanged when needed.
  - No embedding or LLM call is made at submission time. Related terms
    such as atoms, molecules and particles must not be treated as equivalent.
    Unknown synonyms require review before adding them to the registry.
  - **The answer must never be a symbolic/algebraic expression** (e.g.
    "side × side", "(side)^2", "l × b") — those have many equivalent valid
    phrasings that a synonym-matching mechanism cannot reliably normalize
    against each other, which produces false "incorrect" grades for a
    student who reasoned correctly but phrased it differently. See section
    9a: when a concept is computational, the question should instantiate
    concrete numbers and expect a numeric answer instead.
- **Matching:**
  - UI: drag-and-drop. The left-hand list is presented top-to-bottom on the
    left of the screen; the right-hand list (the answer options to drag)
    is presented below/alongside it. The user drags each answer block onto
    the left-hand statement it matches.
  - **Scoring: a matching question is worth exactly 1 mark total** — full
    credit only if every pair in that question is matched correctly, no
    partial credit per pair.
  - **Feedback: every row/pair is visually marked green (correct) or red
    (incorrect) after submission** — this per-item green/red feedback
    convention applies across all question types wherever there are
    multiple parts to a single question, not just matching.
- Every incorrect answer is paired with an explanation grounded in the
  source textbook chunk the question was generated from.

## 8a. Post-quiz solutions review

- **Not shown automatically at submission** — the results page shows the
  overall score plus the four options in section 5, step 11. Per-question
  right/wrong detail and explanations appear only if the user chooses
  "see solutions."
- **The solutions screen reopens the exact quiz just taken, read-only:**
  every question is shown again with the user's submitted answer, marked
  green (correct) or red (incorrect) per the convention in section 8
  (including per-row marking for matching questions), with the source-
  grounded explanation/solution displayed **below each question**.
- **Solution formatting:** explanations are grounded in the same source
  chunk the question was generated from (section 6), and — where an
  official answer example exists in the captured reference corpus
  (in-text exercise answers or CBSE answer keys, section 6) for that
  class/subject — the LLM uses it as a **style reference**, so the
  explanation's structure resembles how NCERT/CBSE present solutions
  (e.g., stepwise working, point-format reasoning), not generic
  free-form AI explanation prose. This is best-effort: not every
  topic/difficulty will have a matching reference example available.
- This screen is available any time after the quiz is submitted — it is
  not time-limited or one-time-view-only in this version.

## 9. Reliability requirements for question generation

- **API-level failures** (timeout, rate limit, provider outage): retry with
  backoff for a bounded number of attempts; if still failing, fall back to
  a secondary LLM provider. If both fail, the user sees a clear error and
  can retry — never an indefinite wait.
- **Sequential fallback, not parallel racing (confirmed decision):**
  providers are tried one at a time, not called simultaneously. Racing
  multiple providers at once would reduce latency slightly but means
  paying for every call made even when discarded, for a failure mode
  (provider outage) that should be rare. Revisit only if provider latency
  becomes a real, observed user-facing problem — not a v1 default.
- **Malformed output** (fails schema validation): automatically regenerate
  that question, up to a bounded number of retries.
- **Persistent failure on a single question** after retries: drop it and
  generate a replacement, so the user still receives the exact number of
  questions requested.
- **Known limitation (out of scope for v1):** no automated fact-grounding
  verification at generation time beyond format validity. Accepted risk,
  to be caught via manual testing rather than a runtime check.

## 9a. Question phrasing quality (concept testing, not verbatim recall)

The app is testing whether the student understands and can reason about the
class-level subject material — not whether they can recall the textbook's
exact sentence structure or notation. Two failure modes observed in v1
testing, both traced to the generation prompts rather than the grading
logic, motivated this explicit requirement:

- **Verbatim-recall phrasing:** a question hook built by lightly editing a
  textbook sentence tests memorization of that specific sentence, not the
  underlying concept. Prompts must instruct the model to use the subject's
  standard terminology (the correct technical terms, kept as-is) while
  rephrasing the question itself in the model's own words, not the source
  text's exact wording.
- **Symbolic-formula fill-in-blank answers:** e.g. a question whose blank
  expects "length × length" as the answer accepts only that phrasing (or
  whatever a similarity check happens to consider close), while a student
  who correctly answers "(side)^2" — mathematically identical — is marked
  wrong. **Fix at the source, not the grader:** when a concept is
  computational (has a formula behind it), the question must instantiate
  concrete numeric values and ask for the computed numeric result (e.g.
  "A square has a side of 6 cm. What is its area, in cm²?" → "36"), not
  ask the student to reproduce the formula itself. Numeric answers are
  graded by exact match (section 8) and have no phrasing-ambiguity problem
  at all. A fill-in-blank answer that is a bare fact/term/notation (e.g. a
  chemical formula, a named process) is unaffected by this rule — it's
  only formula-shaped expressions with an operator (×, ^, /, parentheses)
  that are disallowed as answers. Reviewed chemical notation such as
  `Ca(OH)2` is permitted under section 8's chemical equivalence rules.

This is enforced at generation time (schema validation rejects a
formula-shaped fill-in-blank answer and triggers the section 9 retry path),
not by trying to make the grader smarter about recognizing that two
symbolic expressions are equivalent.

## 10. Question type distribution

When multiple question types are selected, the requested question count is
split as evenly as possible across the selected types, with any remainder
assigned to the first selected type (e.g., 25 across MCQ/fill-in-blank/
matching → 9/8/8). Fixed, non-content-aware rule for v1 (see section 15).

## 10a. Coverage rule for "all topics" / "all chapters" quizzes

To guarantee every sub-unit is actually tested when a user requests full
chapter or full subject coverage:

- **"All topics" (chapter-wide quiz):** the requested question count must be
  **greater than** the number of topics in that chapter.
- **"All chapters" (subject-wide quiz):** the requested question count must
  be **greater than** the number of chapters in that subject.
- At the "select number of questions" step, if the user has chosen "all
  topics" or "all chapters," the app only offers the fixed options (5, 10,
  15, 20, 25) that satisfy the ">" requirement above — e.g., a chapter with
  6 topics requires more than 6, so the smallest valid option offered is
  10 (not 5), since 7/8/9 aren't available as fixed choices.
- **Distribution across sub-units:** equal weighting per topic (or per
  chapter) — not content/length-weighted in v1. The remainder (since the
  count won't usually divide evenly) is distributed **round-robin** across
  sub-units rather than always to the same one, so repeated quizzes on the
  same scope don't systematically favor the same topic/chapter every time.
- **Accuracy-weighted selection:** when the user has enough prior
  performance data (see section 11a) for the sub-units in this scope,
  selection is weighted toward the sub-units where the user's accuracy is
  lowest — so weaker topics are served more often, not just rotated
  fairly. Sub-units with no data yet, or below the minimum sample size
  (section 11a), fall back to random selection instead of being treated as
  "weak." This applies both to the remainder distribution above and to the
  25+ sub-unit overflow case below.
- **Edge case — a chapter/subject with more sub-units than the 25-question
  cap allows (e.g., 25+ chapters in a subject):** the 25-question cap is
  not raised. Instead, the app simply produces the default maximum of 25
  questions, distributed across as many sub-units as that allows — full
  coverage in a single attempt is not guaranteed in this edge case, and
  that's accepted for v1. Which sub-units are included is decided by the
  same accuracy-weighted (with random fallback) selection described above,
  so a student retaking the same subject-wide quiz repeatedly is more
  likely to see chapters they're weak in, while still eventually sampling
  across all chapters over time rather than always hitting the same subset.
  Expected to be a rare case for NCERT content.

## 11. Performance history

Stored per user, per quiz attempt:
- Scope (class, subject, chapter(s), topic(s)), difficulty, date/time, and
  overall score (X correct out of Y).
- Per-question results within that attempt: question text, type, the
  user's answer, whether it was correct.

From this stored data, the user-facing performance section can show:
overall accuracy, accuracy broken down by subject/chapter, accuracy by
difficulty level, accuracy by question type, and attempts over time. This
is derived/aggregated from the stored attempt data, not tracked separately.

## 11a. Accuracy calculation and update mechanism

To keep this efficient as history grows, accuracy is **not** recomputed by
scanning all raw answer history on every read. Instead:

- **Running counters, updated incrementally.** For each user, maintain a
  `correct_count` / `total_count` pair at the finest level:
  `(user, topic, question_type)`. Accuracy at that level is simply
  `correct_count / total_count`.
- **On quiz submission:** group the user's answers by `(topic,
  question_type)` and apply one incremental update per group (not one
  update per question) — increment `total_count` by the group size and
  `correct_count` by the number correct in that group.
- **Higher-level accuracy is summed, not stored separately.** Since every
  topic belongs to exactly one chapter → one subject → one class, chapter/
  subject/class/overall accuracy is computed by summing the relevant leaf
  `(topic, question_type)` counters at read time. This avoids keeping many
  separate aggregate tables in sync, while still being fast, since the
  number of distinct topics a user has touched stays small.
- **Minimum sample size before a topic counts as "weak":** a sub-unit needs
  at least a small minimum number of attempts (suggested: 3) before its
  accuracy is trusted for weak-topic weighting (section 10a). Below that
  threshold, treat it the same as "no data" — random selection applies.
- **Lifetime accuracy, not recency-weighted, for v1.** Accuracy reflects a
  user's full history on that topic/type, not just recent attempts. A
  recency-weighted version (so improvement over time is reflected faster)
  is a reasonable future enhancement but out of scope for v1.

## 12. Outcomes that define success

- A quiz contains only content actually present in the selected
  class/subject/chapter/topic scope — no invented or misscoped content.
- Every question has exactly one defensible correct answer (or correct
  pairing set, for matching) at the requested difficulty.
- A user always receives exactly the number of questions requested, even if
  some individual generations or bank matches fail and need replacing.
- Retaking the same scope reliably surfaces new-to-that-user questions
  wherever the bank/generation can support it.
- Incorrect answers come with a source-grounded explanation, not just a
  right/wrong marker.
- Quiz generation feels fast enough for real use.
- **Latency target: a quiz should be delivered within roughly 10–20
  seconds**, even when generation is needed. Implications (detailed in
  PLAN.md): (a) generation calls for different topics/allocations in one
  request run **in parallel**, not sequentially, so total wait is about
  one LLM call's duration rather than several stacked; (b) **overgeneration
  must not block the user** — the user's exact requested quiz is served
  first, and any extra questions are generated/validated/banked in the
  background afterward, so bank pre-filling never adds to the user's wait.
  The slowest case is a brand-new topic on a cold bank; the common case
  over time is served largely or fully from the bank with few or no LLM
  calls.

## 13. Explicit non-goals (for this version)

- No teacher role, class rosters, quiz-assignment, or gradebook.
- No assertion-reason (either form) or multi-correct-choice question types
  — limited to MCQ, fill-in-blank, and matching for v1.
- Not covering boards other than CBSE/NCERT.
- Not generating diagram-based or image-based questions (text-only) in
  v1. Confirmed as a **planned v2 feature**, not abandoned — this would
  need a multimodal LLM (not just text generation), a new question type
  in the UI/answer format, and new grading logic. Image + caption data is
  captured and stored starting in v1 (section 6) specifically so this can
  be built later without re-processing the source PDFs.
- Not generating questions patterned on real exercise questions or
  previous CBSE papers in v1. Confirmed as a **planned v2 feature**, not
  abandoned — exercise questions and CBSE papers are captured and stored
  separately (section 6) as a future style/pattern reference corpus, but
  are not used for generation yet. When built, this needs an explicit
  instruction to the LLM to generate a genuinely different question
  testing the same concept (not a reworded near-copy) — both for real
  educational value and to avoid reproducing CBSE/NCERT's own content
  too closely.
  - **Correction/finding (confirmed):** CBSE only conducts board exams for
    **Class 10 and Class 12** — there is no official CBSE-published paper
    for any other class. **Resolved decision:** for classes without an
    official CBSE paper, the reference corpus uses NCERT's own in-text
    exercise questions **and their official printed answers** as the
    exam-style/format reference instead. Official CBSE papers (with their
    answer keys) are used for Class 10 and 12. Unofficial third-party
    "CBSE-style" content (ed-tech/tutoring sites) is **not used** — this
    fully replaces the earlier open question rather than leaving it
    unapproved.
- Not adapting difficulty in real time based on live performance.
- Not mixing multiple difficulty levels within a single quiz.
- No automated fact-grounding verification at generation time (section 9).
- No custom-trained/fine-tuned model for question generation — the question
  bank (section 7) is the v1 mechanism for reducing LLM cost over time; a
  fine-tuned model may be reconsidered later, once real usage data exists.

## 14. Constraints on the user experience

- A user should never see a malformed or incomplete question.
- A user should never be asked a question outside the scope they selected.
- The flow should never ask a question already made redundant by a prior
  answer ("all chapters"/"all topics" must skip the now-unnecessary prompt).
- Grading feedback must always be traceable back to real textbook content.

## 15. Open questions to resolve before planning

### 2026-10-29 matching and latency correction

Matching exact identity uses the complete left-to-right mapping, preserving
chemical case and ignoring display order/instruction wording. Different pair
sets with a generic instruction are distinct questions. Embeddings include the
mapping, not only the generic instruction. Legacy questions remain readable and
their mappings are checked during intake without rewriting historical records.
Matching labels must be unique on both sides, so each drag target has a single
identity. Reject repeated labels at generation time and exclude malformed
legacy sets from bank selection. Reset drag state when the question changes.

Solutions read the explanation cache in one batch, generate only missing
explanations concurrently, and persist them on the request thread. Provider
concurrency is bounded by LLM_MAX_WORKERS (default 6) per batch. Larger quizzes
therefore run in bounded waves; constant latency is not promised. Generation
also uses this cap. Database connections are never shared with these workers.
Catalog and source chunks are cached in bounded process-local caches; restart
the service when replacing the textbook corpus. Provider failure still fails
the request atomically, without committing a partial explanation batch.

- **Usage limits:** a soft daily cap on quiz generations per user was
  proposed, purely to control LLM cost during early testing/development.
  Needs a concrete number before PLAN.md (suggested starting point: 10/day,
  adjustable).
- **Synonym-matching mechanism for fill-in-blank text answers (resolved
  2026-10-29):** canonical/accepted answers first, a reviewed chemical
  equivalence registry and reviewed ordinary-term groups; no embedding fallback.
  No extra LLM grading call. See section 8 and PLAN.md section 12.


### 2026-10-29 source quality and progressive solutions

- The student solutions page requests a cache-only review first. Correct answers
  and saved verdicts appear without waiting for generation. Only the selected
  question requests a missing explanation; failure is isolated to that question
  with a Retry control. Cached explanations are reused. The legacy bulk API
  remains supported. Attempt ownership and question membership remain required.
- Submission and requiz review do not load the embedding model. Generation
  embeds successful candidates in a batch, internally capped at four by ONNX.
- Grounding retrieval includes the chapter to prevent same-named topic leakage.
- Preserve lowered formula digits by PDF glyph geometry. Apply versioned exact
  corrections to eight affected class 10 Science chapter-one corpus chunks.
  Do not globally replace valid formulas such as FeO. Older bank rows from these
  chunks are excluded from new quizzes until reviewed; historical grades stay
  unchanged and their explanation shows a source-quality notice. Newly generated
  rows carry the repaired source revision. This is not universal fact validation.
- Offline prefill generates review drafts across three difficulties and three
  types. Only explicitly approved candidates with unchanged scoped sources can
  be imported. No student quota or seen-history writes occur. It is a manual
  operational workflow, not a scheduled worker or a guarantee of bank coverage.
