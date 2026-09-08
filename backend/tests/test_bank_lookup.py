"""Task 3: bank lookup (Step B) against a small manually-seeded test bank."""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bank_lookup import lookup_bank

SCOPE = dict(class_="10", subject="Science", chapter="Chemical Reactions and Equations", topic="Chemical Equations", difficulty="medium")


def seed_question(conn, cluster_id=None, **overrides):
    row = {
        **SCOPE,
        "question_type": "MCQ",
        "text": "sample question text " + str(uuid.uuid4()),
        "source_chunk_id": "chunk-abc",
        "text_hash": str(uuid.uuid4()),
        **overrides,
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO questions (class, subject, chapter, topic, difficulty, type, text, payload,
                                    source_chunk_id, text_hash, concept_cluster_id)
            VALUES (%(class_)s, %(subject)s, %(chapter)s, %(topic)s, %(difficulty)s, %(question_type)s,
                    %(text)s, '{}'::jsonb, %(source_chunk_id)s, %(text_hash)s, %(cluster_id)s)
            RETURNING question_id
            """,
            {**row, "cluster_id": cluster_id},
        )
        return str(cur.fetchone()["question_id"])


def mark_seen(conn, user_id, question_id):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO seen_questions (user_id, question_id) VALUES (%s, %s)", (user_id, question_id))


def test_basic_lookup_returns_matching_questions(db_conn):
    q1 = seed_question(db_conn, cluster_id=uuid.uuid4())
    q2 = seed_question(db_conn, cluster_id=uuid.uuid4())
    result = lookup_bank(db_conn, **SCOPE, question_type="MCQ", user_id="user1", count=5)
    assert set(result.question_ids) == {q1, q2}
    assert result.shortfall == 3


def test_per_user_seen_exclusion(db_conn):
    q1 = seed_question(db_conn, cluster_id=uuid.uuid4())
    q2 = seed_question(db_conn, cluster_id=uuid.uuid4())
    mark_seen(db_conn, "user1", q1)

    result_user1 = lookup_bank(db_conn, **SCOPE, question_type="MCQ", user_id="user1", count=5)
    assert result_user1.question_ids == [q2]

    # a DIFFERENT user hasn't seen q1 -- both should come back for them
    result_user2 = lookup_bank(db_conn, **SCOPE, question_type="MCQ", user_id="user2", count=5)
    assert set(result_user2.question_ids) == {q1, q2}


def test_distinct_on_cluster_returns_at_most_one_per_cluster(db_conn):
    shared_cluster = uuid.uuid4()
    q1 = seed_question(db_conn, cluster_id=shared_cluster)
    q2 = seed_question(db_conn, cluster_id=shared_cluster)  # near-dup of q1, same cluster
    q3 = seed_question(db_conn, cluster_id=uuid.uuid4())  # distinct concept

    result = lookup_bank(db_conn, **SCOPE, question_type="MCQ", user_id="user1", count=5)
    assert len(result.question_ids) == 2, "only one of q1/q2 (same cluster) should be returned"
    assert q3 in result.question_ids
    assert len({q1, q2} & set(result.question_ids)) == 1


def test_null_cluster_id_rows_are_not_collapsed_together(db_conn):
    # Defensive case: rows without a cluster_id yet (shouldn't happen post
    # task 9, but the query must not silently treat them as duplicates of
    # each other via DISTINCT ON's NULL-equality behavior).
    q1 = seed_question(db_conn, cluster_id=None)
    q2 = seed_question(db_conn, cluster_id=None)
    result = lookup_bank(db_conn, **SCOPE, question_type="MCQ", user_id="user1", count=5)
    assert set(result.question_ids) == {q1, q2}


def test_shortfall_counted_correctly(db_conn):
    seed_question(db_conn, cluster_id=uuid.uuid4())
    seed_question(db_conn, cluster_id=uuid.uuid4())
    result = lookup_bank(db_conn, **SCOPE, question_type="MCQ", user_id="user1", count=5)
    assert len(result.question_ids) == 2
    assert result.shortfall == 3

    result_full = lookup_bank(db_conn, **SCOPE, question_type="MCQ", user_id="user1", count=2)
    assert result_full.shortfall == 0


def test_scope_isolation_wrong_topic_not_returned(db_conn):
    seed_question(db_conn, cluster_id=uuid.uuid4(), topic="A Different Topic")
    seed_question(db_conn, cluster_id=uuid.uuid4(), difficulty="hard")
    seed_question(db_conn, cluster_id=uuid.uuid4(), question_type="fill_in_blank")
    matching = seed_question(db_conn, cluster_id=uuid.uuid4())

    result = lookup_bank(db_conn, **SCOPE, question_type="MCQ", user_id="user1", count=5)
    assert result.question_ids == [matching]


def test_takes_at_most_count_results(db_conn):
    for _ in range(5):
        seed_question(db_conn, cluster_id=uuid.uuid4())
    result = lookup_bank(db_conn, **SCOPE, question_type="MCQ", user_id="user1", count=3)
    assert len(result.question_ids) == 3
    assert result.shortfall == 0
