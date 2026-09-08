"""Task 11a: verify cross-type similarity scope at the query level.

Seed the bank with an MCQ and a fill-in-blank testing the same fact,
sharing a concept_cluster_id (as task 9's clustering would assign them).
Confirm one quiz assembly (two lookup_bank calls, one per allocation
line, threading exclude_cluster_ids between them) never returns both.
Also confirm a matching question with an overlapping fact — a DIFFERENT
concept_cluster_id, since matching is its own comparison group (task 9) —
is NOT excluded. All via Step B's DISTINCT ON / exclusion filter, no
runtime embedding comparison (bank_lookup.py never touches `embedding`).
"""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bank_lookup import lookup_bank
from app.dedup import compute_text_hash

SCOPE = dict(class_="10", subject="Science", chapter="Chemical Reactions and Equations", topic="Chemical Equations", difficulty="medium")


def _insert(conn, question_type, text, cluster_id):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO questions (class, subject, chapter, topic, difficulty, type, text, payload,
                                    source_chunk_id, text_hash, concept_cluster_id)
            VALUES (%(class_)s, %(subject)s, %(chapter)s, %(topic)s, %(difficulty)s, %(question_type)s,
                    %(text)s, '{}'::jsonb, 'chunk-1', %(text_hash)s, %(cluster_id)s)
            RETURNING question_id
            """,
            {**SCOPE, "question_type": question_type, "text": text, "text_hash": compute_text_hash(text),
             "cluster_id": cluster_id},
        )
        return str(cur.fetchone()["question_id"])


def test_quiz_assembly_never_returns_both_sides_of_a_cross_type_cluster(db_conn):
    shared_cluster = uuid.uuid4()
    mcq_id = _insert(db_conn, "MCQ", "Which gas is produced when zinc reacts with dilute sulphuric acid?", shared_cluster)
    fib_id = _insert(db_conn, "fill_in_blank", "Zinc reacting with dilute sulphuric acid produces ____ gas.", shared_cluster)

    # Simulates the quiz-assembly loop (task 11): request both types for
    # the same topic/difficulty as two allocation lines, threading
    # cluster ids used by earlier lines into later ones.
    mcq_result = lookup_bank(db_conn, **SCOPE, question_type="MCQ", user_id="user1", count=5)
    assert mcq_result.question_ids == [mcq_id]

    fib_result = lookup_bank(
        db_conn, **SCOPE, question_type="fill_in_blank", user_id="user1", count=5,
        exclude_cluster_ids=mcq_result.cluster_ids,
    )
    assert fib_result.question_ids == [], "fill-in-blank sharing the MCQ's cluster must be excluded from this quiz"


def test_matching_with_overlapping_fact_is_not_excluded(db_conn):
    # A matching question about the SAME underlying fact family, but
    # clustered separately (different comparison group per task 9) --
    # confirms exclusion is scoped to actual shared cluster ids, not a
    # blanket "same topic" exclusion.
    mcq_cluster = uuid.uuid4()
    matching_cluster = uuid.uuid4()  # a real assign_cluster_id call would never merge these (task 9)
    mcq_id = _insert(db_conn, "MCQ", "Which gas is produced when zinc reacts with dilute sulphuric acid?", mcq_cluster)
    matching_id = _insert(db_conn, "matching", "Match each reactant to the gas it produces.", matching_cluster)

    mcq_result = lookup_bank(db_conn, **SCOPE, question_type="MCQ", user_id="user1", count=5)
    matching_result = lookup_bank(
        db_conn, **SCOPE, question_type="matching", user_id="user1", count=5,
        exclude_cluster_ids=mcq_result.cluster_ids,
    )
    assert matching_result.question_ids == [matching_id], "matching question must NOT be excluded by an MCQ's cluster"


def test_exclusion_accumulates_across_more_than_two_allocation_lines(db_conn):
    shared_cluster = uuid.uuid4()
    mcq_id = _insert(db_conn, "MCQ", "text a", shared_cluster)
    fib_id = _insert(db_conn, "fill_in_blank", "text b", shared_cluster)

    used_clusters: list[str] = []
    mcq_result = lookup_bank(db_conn, **SCOPE, question_type="MCQ", user_id="user1", count=5, exclude_cluster_ids=used_clusters)
    used_clusters += mcq_result.cluster_ids

    fib_result = lookup_bank(db_conn, **SCOPE, question_type="fill_in_blank", user_id="user1", count=5, exclude_cluster_ids=used_clusters)
    used_clusters += fib_result.cluster_ids

    assert mcq_result.question_ids == [mcq_id]
    assert fib_result.question_ids == []
