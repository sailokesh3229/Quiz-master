-- Section 2, PLAN.md section 4a: data model and database architecture.
-- PostgreSQL + pgvector, single database, write-time similarity work,
-- pure indexed relational lookup on the serving path.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid()

CREATE TABLE IF NOT EXISTS questions (
    question_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    class               TEXT NOT NULL,
    subject             TEXT NOT NULL,
    chapter             TEXT NOT NULL,
    topic               TEXT NOT NULL,
    difficulty          TEXT NOT NULL CHECK (difficulty IN ('easy', 'medium', 'hard')),
    type                TEXT NOT NULL CHECK (type IN ('MCQ', 'fill_in_blank', 'matching')),
    text                TEXT NOT NULL,
    payload             JSONB NOT NULL,   -- full type-specific structure: options+correct index (MCQ),
                                           -- blank+answer (fill_in_blank), pairs (matching)
    source_chunk_id     TEXT NOT NULL,    -- Section 1 chunk_id, for grounded explanations (SPEC 8a)
    text_hash           TEXT NOT NULL,    -- normalized SHA-256, exact-dup detection (section 5)
    embedding           vector(384),      -- all-MiniLM-L6-v2 dim, same model as Section 1
    concept_cluster_id  UUID,             -- assigned at write time (section 5) — the single mechanism
                                           -- for same-type AND cross-type near-dup exclusion at read time
    explanation         TEXT,             -- Section 3 (SPEC 8a): source-grounded, reference-corpus-styled
                                           -- explanation, generated lazily on first solutions-screen view
                                           -- and cached here (not user-specific, safe to share across users)
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Serving-path indexes (no vector touched at read time — section 4a):
CREATE INDEX IF NOT EXISTS idx_questions_scope
    ON questions (class, subject, chapter, topic, difficulty, type);
CREATE INDEX IF NOT EXISTS idx_questions_cluster
    ON questions (concept_cluster_id);
CREATE INDEX IF NOT EXISTS idx_questions_texthash
    ON questions (class, subject, chapter, topic, text_hash);
-- Write-time-only index (ANN search during intake, section 5):
CREATE INDEX IF NOT EXISTS idx_questions_embedding
    ON questions USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS seen_questions (
    user_id       TEXT NOT NULL,
    question_id   UUID NOT NULL REFERENCES questions(question_id),
    delivered_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, question_id)
);
CREATE INDEX IF NOT EXISTS idx_seen_questions_user ON seen_questions (user_id, question_id);

-- Migration (2026-09-08): performance_counters used to be keyed on
-- (user_id, topic, question_type) alone, silently assuming topic names are
-- globally unique across the corpus (SPEC 11a's own documented "every
-- topic belongs to exactly one chapter" assumption). Confirmed false at
-- corpus scale: "Introduction" alone is a real topic name shared by 255
-- different chapters (classes 6-12), and even bare CHAPTER names collide
-- across subjects (e.g. "Thermodynamics" in both Physics and Chemistry) --
-- so a user's accuracy on one chapter's "Introduction" was being merged
-- with every other chapter's "Introduction", corrupting both the
-- dashboard's by-chapter rollup (performance_service.py) and the
-- accuracy-weighted quiz selection (scope_resolution.py) for any request
-- spanning colliding topic names -- which "all chapters"/subject-wide
-- quizzes commonly do, since "Introduction" repeats in nearly every
-- chapter. Re-keyed on the full (class, subject, chapter, topic) identity.
-- Old rows are dropped rather than migrated: their own stored numbers are
-- exactly the cross-chapter-contaminated data this fix exists to prevent,
-- so there is nothing trustworthy to carry forward. No-ops on a fresh DB
-- (table doesn't exist yet) and on an already-upgraded one (chapter column
-- already present).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'performance_counters')
       AND NOT EXISTS (
           SELECT 1 FROM information_schema.columns
           WHERE table_name = 'performance_counters' AND column_name = 'chapter'
       )
    THEN
        DROP TABLE performance_counters;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS performance_counters (
    user_id         TEXT NOT NULL,
    class           TEXT NOT NULL,
    subject         TEXT NOT NULL,
    chapter         TEXT NOT NULL,
    topic           TEXT NOT NULL,
    question_type   TEXT NOT NULL,
    correct_count   INT NOT NULL DEFAULT 0,
    total_count     INT NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, class, subject, chapter, topic, question_type)
);

-- Requiz replays are intentionally NOT written here (SPEC section 11 /
-- PLAN Step E: "requiz path skips the history-update logic entirely") —
-- only real retakes (new unseen questions) get a row.
CREATE TABLE IF NOT EXISTS quiz_attempts (
    attempt_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        TEXT NOT NULL,
    class          TEXT NOT NULL,
    subject        TEXT NOT NULL,
    scope          JSONB NOT NULL,  -- {"scope_type":..., "sub_units": [...]}
    difficulty     TEXT NOT NULL,
    score_correct  INT NOT NULL,
    score_total    INT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_quiz_attempts_user ON quiz_attempts (user_id, created_at);

CREATE TABLE IF NOT EXISTS attempt_answers (
    attempt_id    UUID NOT NULL REFERENCES quiz_attempts(attempt_id),
    question_id   UUID NOT NULL REFERENCES questions(question_id),
    user_answer   JSONB NOT NULL,
    is_correct    BOOLEAN NOT NULL,
    PRIMARY KEY (attempt_id, question_id)
);

-- Section 3 (PLAN.md section 8): user_id everywhere above is Clerk's user
-- id (a string) — Clerk owns auth/session/password/OAuth entirely, so the
-- only app-specific per-user state left to store here is the profile bit
-- SPEC section 4 asks for: a default class, to speed up repeat use.
CREATE TABLE IF NOT EXISTS profiles (
    user_id        TEXT PRIMARY KEY,
    default_class  TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- SPEC section 15: soft daily cap on quiz generations per user, purely to
-- control LLM cost during early testing (config value, section 3 task 8
-- wiring — see app/config.py DAILY_QUIZ_GENERATION_LIMIT). One row per
-- create-quiz call (a "generation" is the act of assembling a quiz,
-- whether or not the user submits it) — deliberately separate from
-- quiz_attempts, which only gets a row on a real (non-requiz) submission.
CREATE TABLE IF NOT EXISTS quiz_generation_log (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_quiz_generation_log_user_time
    ON quiz_generation_log (user_id, created_at);

-- Separate reference-corpus table (PLAN.md section 4, not part of the
-- chunk record) — in-text exercise Q&A already extracted by Section 1
-- (ingestion/scripts/extract_exercises.py); this is where it lands for
-- the app to query as a style reference (SPEC 8a).
CREATE TABLE IF NOT EXISTS reference_corpus (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    class           TEXT NOT NULL,
    subject         TEXT NOT NULL,
    chapter         TEXT NOT NULL,
    topic           TEXT,
    question        TEXT NOT NULL,
    -- Nullable (2026-09-02, SPEC.md section 6): a source='ncert_activity'
    -- row has no printed answer at all (an open-ended/activity-style
    -- exercise question, captured purely as a question-wording style
    -- reference for generation) — see app/explanations.py's style-
    -- reference query, which must exclude these (it needs a real Q+A pair).
    answer          TEXT,
    source          TEXT NOT NULL DEFAULT 'ncert_intext'  -- vs. 'cbse_paper' once sourced (SPEC 13); 'ncert_activity' = no printed answer
);
