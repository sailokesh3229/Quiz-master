"""Section 2, section 5: duplicate handling, precomputed at write time.

Task 8: exact-hash duplicate detection.
Task 9: write-time embedding clustering (concept_cluster_id assignment).
Task 10: within-batch clustering for a background overgeneration batch.

All similarity work happens at question intake (a background operation
off the request path, per PLAN.md section 2's latency rules) — serving
(bank_lookup.py) stays a pure relational lookup.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass

import psycopg

# Single-fact types cluster together; matching (tests 4-5 facts) is
# compared only against other matching questions (PLAN.md section 5:
# "a matching question tests 4-5 facts, so it isn't a dup of a
# single-fact question").
_COMPARISON_GROUPS = {
    "MCQ": "single_fact",
    "fill_in_blank": "single_fact",
    "matching": "matching",
}

# Empirically tuned (2026-08-30, scripts/tune_dedup_threshold*.py) —
# resolves PLAN.md section 5/8's open question. Method: 32 real generated
# questions across 4 different real Section 1 topics, paired into 20
# verified near-duplicate pairs (LLM paraphrases of the same question —
# ground truth by construction) and 475 genuinely-distinct pairs (after
# filtering ~21 incidental duplicates the generation itself produced —
# some facts only support one natural phrasing, e.g. a fill-in-blank
# regenerated at different difficulties came out verbatim-identical).
#
# Findings: near-duplicate cosine similarity ranged 0.830-0.973 (median
# 0.925); genuinely-distinct pairs reached up to 0.910 in the hardest
# case (same narrow topic) — and that one outlier ("the reddish brown
# powder... when it rusts" vs "...due to corrosion") is itself arguably a
# real near-duplicate my text-based contamination filter missed (low
# lexical overlap, same underlying fact), not a genuine false-merge risk.
# The error-minimizing threshold was similarity >= 0.867 (3/495 pairs
# wrong); similarity >= 0.90 (distance <= 0.10) was chosen instead per
# PLAN.md's "start conservative" guidance — catches 12/20 (60%) of
# deliberately-constructed paraphrases at ~zero real false merges. Was
# 0.15 (similarity >= 0.85) before this data existed — that guess turned
# out slightly too permissive (3 false merges at 0.85 in this dataset).
# pgvector cosine distance is 0 (identical) to 2 (opposite).
DEFAULT_CLUSTER_DISTANCE_THRESHOLD = 0.10


def normalize_text(text: str) -> str:
    """Light normalization for exact-duplicate hashing — case and
    whitespace variance shouldn't defeat exact-dup detection, but this is
    NOT semantic matching (that's the embedding step, task 9)."""
    return re.sub(r"\s+", " ", text.strip().lower())


def compute_text_hash(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


@dataclass
class IntakeResult:
    accepted: bool
    question_id: str | None = None
    reason: str | None = None  # "exact_duplicate" | "accepted"
    duplicate_of: str | None = None
    concept_cluster_id: str | None = None


def check_exact_duplicate(
    conn: psycopg.Connection, class_: str, subject: str, chapter: str, topic: str, text_hash: str
) -> str | None:
    """Section 5, step 1: exact-duplicate check against the SAME SCOPE.
    Returns the existing question_id if a duplicate is found, else None."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT question_id FROM questions
            WHERE class = %s AND subject = %s AND chapter = %s AND topic = %s AND text_hash = %s
            LIMIT 1
            """,
            (class_, subject, chapter, topic, text_hash),
        )
        row = cur.fetchone()
        return str(row["question_id"]) if row else None


def assign_cluster_id(
    conn: psycopg.Connection,
    class_: str,
    subject: str,
    topic: str,
    difficulty: str,
    question_type: str,
    embedding: list[float],
    threshold: float = DEFAULT_CLUSTER_DISTANCE_THRESHOLD,
) -> str:
    """Section 5, step 2-3: ANN search against existing bank questions in
    the same topic+difficulty, within the restricted comparison group
    (single-fact vs. matching). Inherits the nearest match's cluster if
    within threshold, else mints a new one."""
    group = _COMPARISON_GROUPS[question_type]
    group_types = [t for t, g in _COMPARISON_GROUPS.items() if g == group]

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT concept_cluster_id, embedding <=> %(embedding)s AS distance
            FROM questions
            WHERE class = %(class_)s AND topic = %(topic)s AND difficulty = %(difficulty)s
              AND type = ANY(%(group_types)s)
              AND embedding IS NOT NULL
            ORDER BY embedding <=> %(embedding)s
            LIMIT 1
            """,
            {
                "embedding": str(embedding),
                "class_": class_,
                "topic": topic,
                "difficulty": difficulty,
                "group_types": group_types,
            },
        )
        nearest = cur.fetchone()

    if nearest and nearest["distance"] is not None and nearest["distance"] <= threshold:
        return str(nearest["concept_cluster_id"])
    return str(uuid.uuid4())


def assign_cluster_id_within_batch(
    conn: psycopg.Connection,
    class_: str,
    subject: str,
    topic: str,
    difficulty: str,
    question_type: str,
    embedding: list[float],
    batch_so_far: list[dict],
    threshold: float = DEFAULT_CLUSTER_DISTANCE_THRESHOLD,
) -> str:
    """Task 10: cluster a new question against BOTH the persisted bank
    (assign_cluster_id) AND every question already processed earlier in
    this same background batch — so two near-identical new questions
    generated in one overgeneration run get the same cluster id and can
    never both land in the bank as separate entries.

    batch_so_far: [{"embedding": [...], "topic":..., "difficulty":...,
      "question_type":..., "concept_cluster_id": "..."}] for questions
      already processed in this batch, earliest first.
    """
    group = _COMPARISON_GROUPS[question_type]

    best_distance = None
    best_cluster = None
    for prior in batch_so_far:
        if prior["topic"] != topic or prior["difficulty"] != difficulty:
            continue
        if _COMPARISON_GROUPS[prior["question_type"]] != group:
            continue
        distance = _cosine_distance(embedding, prior["embedding"])
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_cluster = prior["concept_cluster_id"]

    bank_cluster = assign_cluster_id(conn, class_, subject, topic, difficulty, question_type, embedding, threshold)

    if best_distance is not None and best_distance <= threshold:
        # a within-batch sibling is at least as close as anything in the
        # bank (or the bank had no match at all) -> inherit its cluster
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM questions WHERE concept_cluster_id = %s LIMIT 1", (best_cluster,))
            already_in_bank = cur.fetchone() is not None
        if not already_in_bank:
            return best_cluster
        # the sibling's cluster already exists in the bank under a
        # different id than what assign_cluster_id found -- prefer the
        # bank's own answer for consistency with persisted data
        return bank_cluster

    return bank_cluster


def _cosine_distance(a: list[float], b: list[float]) -> float:
    import math

    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 1.0
    return 1.0 - dot / (norm_a * norm_b)


def intake_question(
    conn: psycopg.Connection,
    class_: str,
    subject: str,
    chapter: str,
    topic: str,
    difficulty: str,
    question_type: str,
    text: str,
    payload: dict,
    source_chunk_id: str,
    embedding: list[float],
    batch_so_far: list[dict] | None = None,
) -> IntakeResult:
    """Full write-time intake pipeline (section 5): exact-hash check, then
    embedding clustering (within-batch aware if batch_so_far given), then
    insert. Returns accepted=False (no insert) on an exact duplicate."""
    text_hash = compute_text_hash(text)
    dup_id = check_exact_duplicate(conn, class_, subject, chapter, topic, text_hash)
    if dup_id:
        return IntakeResult(accepted=False, reason="exact_duplicate", duplicate_of=dup_id)

    if batch_so_far is not None:
        cluster_id = assign_cluster_id_within_batch(
            conn, class_, subject, topic, difficulty, question_type, embedding, batch_so_far
        )
    else:
        cluster_id = assign_cluster_id(conn, class_, subject, topic, difficulty, question_type, embedding)

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO questions (class, subject, chapter, topic, difficulty, type, text, payload,
                                    source_chunk_id, text_hash, embedding, concept_cluster_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING question_id
            """,
            (class_, subject, chapter, topic, difficulty, question_type, text, psycopg.types.json.Json(payload),
             source_chunk_id, text_hash, str(embedding), cluster_id),
        )
        question_id = str(cur.fetchone()["question_id"])

    return IntakeResult(accepted=True, question_id=question_id, reason="accepted", concept_cluster_id=cluster_id)
