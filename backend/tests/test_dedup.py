"""Tasks 8-10: exact-hash dedup, write-time embedding clustering, and
within-batch clustering (Section 2, section 5)."""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.dedup import (
    assign_cluster_id,
    assign_cluster_id_within_batch,
    check_exact_duplicate,
    compute_text_hash,
    intake_question,
    normalize_text,
)

SCOPE = dict(class_="10", subject="Science", chapter="Chemical Reactions and Equations", topic="Chemical Equations")


def _insert(conn, question_type="MCQ", difficulty="medium", text="q", embedding=None, cluster_id=None):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO questions (class, subject, chapter, topic, difficulty, type, text, payload,
                                    source_chunk_id, text_hash, embedding, concept_cluster_id)
            VALUES (%(class_)s, %(subject)s, %(chapter)s, %(topic)s, %(difficulty)s, %(question_type)s,
                    %(text)s, '{}'::jsonb, 'chunk-1', %(text_hash)s, %(embedding)s, %(cluster_id)s)
            RETURNING question_id
            """,
            {
                **SCOPE,
                "question_type": question_type,
                "difficulty": difficulty,
                "text": text,
                "text_hash": compute_text_hash(text),
                "embedding": str(embedding) if embedding else None,
                "cluster_id": cluster_id,
            },
        )
        return str(cur.fetchone()["question_id"])


# ---------------------------------------------------------------------------
# Task 8: exact-hash duplicate detection
# ---------------------------------------------------------------------------


def test_normalize_text_ignores_case_and_whitespace():
    assert normalize_text("  What  is   H2O?  ") == normalize_text("what is h2o?")


def test_exact_duplicate_detected(db_conn):
    text = "What gas is produced when zinc reacts with dilute sulphuric acid?"
    existing_id = _insert(db_conn, text=text)
    dup = check_exact_duplicate(db_conn, **SCOPE, text_hash=compute_text_hash(text))
    assert dup == existing_id


def test_exact_duplicate_case_and_whitespace_insensitive(db_conn):
    existing_id = _insert(db_conn, text="What gas is produced?")
    dup = check_exact_duplicate(db_conn, **SCOPE, text_hash=compute_text_hash("  WHAT GAS   is produced?  "))
    assert dup == existing_id


def test_no_duplicate_for_different_text(db_conn):
    _insert(db_conn, text="What gas is produced when zinc reacts with acid?")
    dup = check_exact_duplicate(db_conn, **SCOPE, text_hash=compute_text_hash("A completely different question."))
    assert dup is None


def test_duplicate_check_scoped_to_same_topic(db_conn):
    # same text, but a DIFFERENT topic -- section 5 says "same scope", so
    # this must not count as a duplicate across topics
    text = "shared wording"
    _insert(db_conn, text=text)
    dup = check_exact_duplicate(
        db_conn, class_="10", subject="Science", chapter="Chemical Reactions and Equations",
        topic="A Different Topic", text_hash=compute_text_hash(text),
    )
    assert dup is None


def test_intake_rejects_exact_duplicate_end_to_end(db_conn, embed_model):
    text = "What gas is produced when zinc reacts with dilute sulphuric acid?"
    embedding = embed_model.encode(text).tolist()
    first = intake_question(db_conn, **SCOPE, difficulty="medium", question_type="MCQ",
                             text=text, payload={}, source_chunk_id="chunk-1", embedding=embedding)
    assert first.accepted

    second = intake_question(db_conn, **SCOPE, difficulty="medium", question_type="MCQ",
                              text=text, payload={}, source_chunk_id="chunk-1", embedding=embedding)
    assert not second.accepted
    assert second.reason == "exact_duplicate"
    assert second.duplicate_of == first.question_id


# ---------------------------------------------------------------------------
# Task 9: write-time embedding clustering
# ---------------------------------------------------------------------------


def test_reworded_mcq_and_fill_in_blank_share_a_cluster(db_conn, embed_model):
    # Same fact, near-identical wording, different types -- both single_fact
    # comparison group, so they SHOULD cluster together (cross-type dedup).
    mcq_text = "Zinc reacting with dilute sulphuric acid produces which gas?"
    fib_text = "Zinc reacting with dilute sulphuric acid produces ____ gas."

    mcq_embedding = embed_model.encode(mcq_text).tolist()
    fib_embedding = embed_model.encode(fib_text).tolist()

    mcq_id = _insert(db_conn, question_type="MCQ", text=mcq_text, embedding=mcq_embedding,
                      cluster_id=(mcq_cluster := uuid.uuid4()))

    cluster = assign_cluster_id(db_conn, class_="10", subject="Science", topic="Chemical Equations",
                                 difficulty="medium", question_type="fill_in_blank", embedding=fib_embedding)
    assert cluster == str(mcq_cluster), "near-identical MCQ/fill-in-blank rewording of the same fact should share a cluster"


def test_matching_never_shares_a_cluster_with_mcq_even_if_identical_text(db_conn, embed_model):
    # Same comparison-group restriction test, but the OPPOSITE case:
    # matching is its own comparison group (PLAN.md section 5), so it must
    # never cluster with MCQ/fill-in-blank -- even with identical text,
    # which would otherwise guarantee a cluster match if groups were merged.
    text = "identical text for both"
    embedding = embed_model.encode(text).tolist()
    mcq_cluster = uuid.uuid4()
    _insert(db_conn, question_type="MCQ", text=text, embedding=embedding, cluster_id=mcq_cluster)

    cluster = assign_cluster_id(db_conn, class_="10", subject="Science", topic="Chemical Equations",
                                 difficulty="medium", question_type="matching", embedding=embedding)
    assert cluster != str(mcq_cluster)


def test_unrelated_question_gets_a_new_cluster(db_conn, embed_model):
    _insert(db_conn, question_type="MCQ", text="What gas is produced when zinc reacts with acid?",
            embedding=embed_model.encode("What gas is produced when zinc reacts with acid?").tolist(),
            cluster_id=uuid.uuid4())

    unrelated_text = "Which planet is known as the Red Planet?"
    cluster = assign_cluster_id(db_conn, class_="10", subject="Science", topic="Chemical Equations",
                                 difficulty="medium", question_type="MCQ",
                                 embedding=embed_model.encode(unrelated_text).tolist())
    # a fresh UUID -- not matched to the existing (unrelated) cluster
    with db_conn.cursor() as cur:
        cur.execute("SELECT concept_cluster_id FROM questions WHERE type='MCQ' LIMIT 1")
        existing_cluster = str(cur.fetchone()["concept_cluster_id"])
    assert cluster != existing_cluster


def test_clustering_scoped_to_topic_and_difficulty(db_conn, embed_model):
    # Same near-identical text, but a different DIFFICULTY -- section 5
    # scopes ANN comparison to "the same topic + difficulty", so this
    # should NOT cluster despite the wording being close.
    text = "Zinc reacts with dilute sulphuric acid to produce hydrogen gas."
    embedding = embed_model.encode(text).tolist()
    other_cluster = uuid.uuid4()
    _insert(db_conn, question_type="MCQ", difficulty="hard", text=text, embedding=embedding, cluster_id=other_cluster)

    cluster = assign_cluster_id(db_conn, class_="10", subject="Science", topic="Chemical Equations",
                                 difficulty="medium", question_type="MCQ", embedding=embedding)
    assert cluster != str(other_cluster)


# ---------------------------------------------------------------------------
# Task 10: within-batch clustering
# ---------------------------------------------------------------------------


def test_within_batch_near_duplicates_share_a_cluster(db_conn, embed_model):
    # Two near-identical NEW questions generated in the same background
    # overgeneration batch -- neither is in the persisted bank yet, so
    # assign_cluster_id alone (bank-only) would give each its own cluster.
    # assign_cluster_id_within_batch must still catch the within-batch
    # near-duplicate.
    text_a = "What gas is produced when zinc reacts with dilute sulphuric acid?"
    text_b = "Which gas is produced when zinc reacts with dilute sulphuric acid?"
    emb_a = embed_model.encode(text_a).tolist()
    emb_b = embed_model.encode(text_b).tolist()

    result_a = intake_question(db_conn, **SCOPE, difficulty="medium", question_type="MCQ",
                                text=text_a, payload={}, source_chunk_id="chunk-1", embedding=emb_a,
                                batch_so_far=[])
    assert result_a.accepted

    batch_so_far = [
        {"embedding": emb_a, "topic": SCOPE["topic"], "difficulty": "medium",
         "question_type": "MCQ", "concept_cluster_id": result_a.concept_cluster_id}
    ]
    result_b = intake_question(db_conn, **SCOPE, difficulty="medium", question_type="MCQ",
                                text=text_b, payload={}, source_chunk_id="chunk-1", embedding=emb_b,
                                batch_so_far=batch_so_far)
    assert result_b.accepted
    assert result_b.concept_cluster_id == result_a.concept_cluster_id


def test_within_batch_unrelated_questions_get_different_clusters(db_conn, embed_model):
    text_a = "What gas is produced when zinc reacts with dilute sulphuric acid?"
    text_b = "Which planet is known as the Red Planet?"
    emb_a = embed_model.encode(text_a).tolist()
    emb_b = embed_model.encode(text_b).tolist()

    result_a = intake_question(db_conn, **SCOPE, difficulty="medium", question_type="MCQ",
                                text=text_a, payload={}, source_chunk_id="chunk-1", embedding=emb_a,
                                batch_so_far=[])
    batch_so_far = [
        {"embedding": emb_a, "topic": SCOPE["topic"], "difficulty": "medium",
         "question_type": "MCQ", "concept_cluster_id": result_a.concept_cluster_id}
    ]
    result_b = intake_question(db_conn, **SCOPE, difficulty="medium", question_type="MCQ",
                                text=text_b, payload={}, source_chunk_id="chunk-1", embedding=emb_b,
                                batch_so_far=batch_so_far)
    assert result_b.concept_cluster_id != result_a.concept_cluster_id
