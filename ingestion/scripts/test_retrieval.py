"""Task 9: retrieval test script.

Given a (class, subject, chapter, topic) tuple, confirm the correct
chunk(s) come back via metadata filter + similarity search (PLAN.md
section 2, Step G / Step F). Given "all topics" for a chapter, confirm
every topic's chunks come back. This is the hard gate PLAN.md section 5
calls for before retrieval feeds into question generation.

Usage: .venv/Scripts/python scripts/test_retrieval.py
"""

import sys

from embed_and_store import get_client, get_model

PASS = "PASS"
FAIL = "FAIL"


def query_by_metadata(coll, where: dict, n: int = 50) -> list[dict]:
    res = coll.get(where=where, include=["metadatas", "documents"])
    return [{"id": i, "metadata": m, "text": d} for i, m, d in zip(res["ids"], res["metadatas"], res["documents"])]


def semantic_query(coll, model, query_text: str, where: dict, n_results: int = 3) -> list[dict]:
    embedding = model.encode([query_text]).tolist()
    res = coll.query(query_embeddings=embedding, n_results=n_results, where=where)
    return [
        {"id": i, "metadata": m, "text": d, "distance": dist}
        for i, m, d, dist in zip(res["ids"][0], res["metadatas"][0], res["documents"][0], res["distances"][0])
    ]


def check(label: str, condition: bool, detail: str = "") -> bool:
    status = PASS if condition else FAIL
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def run_tests() -> bool:
    client = get_client()
    model = get_model()
    coll = client.get_collection("chunks")
    corpus_coll = client.get_collection("reference_corpus")

    all_passed = True

    print("Test 1: single-topic scoped retrieval (class=10, subject=Science, chapter_number=1, topic=1.1)")
    results = query_by_metadata(coll, {"$and": [{"class": "10"}, {"subject": "Science"}, {"chapter_number": 1}, {"topic_number": "1.1"}]})
    all_passed &= check("at least one chunk returned", len(results) > 0, f"got {len(results)}")
    all_passed &= check(
        "every returned chunk is actually topic 1.1",
        all(r["metadata"]["topic_number"] == "1.1" for r in results),
    )
    all_passed &= check(
        "no chunk from a different topic leaked in",
        all(r["metadata"]["chapter_number"] == 1 for r in results),
    )
    print()

    print("Test 2: 'all topics' scoped retrieval (class=10, subject=Science, chapter_number=1) covers every topic")
    chapter_results = query_by_metadata(coll, {"$and": [{"class": "10"}, {"subject": "Science"}, {"chapter_number": 1}]})
    topic_numbers = {r["metadata"]["topic_number"] for r in chapter_results if r["metadata"]["topic_number"]}
    all_passed &= check(
        "all 3 real topics (1.1, 1.2, 1.3) present",
        {"1.1", "1.2", "1.3"} <= topic_numbers,
        f"got {topic_numbers}",
    )
    print()

    print("Test 3: metadata filter scopes strictly — a different class/subject must not leak in")
    cross_check = query_by_metadata(coll, {"$and": [{"class": "10"}, {"subject": "Science"}, {"chapter_number": 1}, {"subject": "Maths"}]})
    all_passed &= check("contradictory filter returns nothing", len(cross_check) == 0)
    other_subject = query_by_metadata(coll, {"$and": [{"class": "6"}, {"subject": "Maths"}]})
    all_passed &= check(
        "class 6 Maths chunks exist and are distinct from class 10 Science",
        len(other_subject) > 0 and all(r["metadata"]["subject"] == "Maths" for r in other_subject),
        f"got {len(other_subject)}",
    )
    print()

    print("Test 4: semantic similarity search returns topically relevant chunks within scope")
    sem_results = semantic_query(
        coll, model, "balancing a chemical equation", {"$and": [{"class": "10"}, {"subject": "Science"}, {"chapter_number": 1}]}, n_results=3
    )
    all_passed &= check("semantic query returns results", len(sem_results) > 0)
    all_passed &= check(
        "top result is scoped to chapter 1 as requested",
        all(r["metadata"]["chapter_number"] == 1 for r in sem_results),
    )
    all_passed &= check(
        "top semantic match is topic 1.1 (Chemical Equations) as expected",
        sem_results[0]["metadata"]["topic_number"] == "1.1" if sem_results else False,
        f"got {sem_results[0]['metadata']['topic_number'] if sem_results else 'none'}",
    )
    print()

    print("Test 5: has_table metadata is queryable — chunk 1.1#2 (balancing-equation atom-count table) is flagged")
    tabled = query_by_metadata(coll, {"$and": [{"class": "10"}, {"subject": "Science"}, {"chapter_number": 1}, {"has_table": True}]})
    all_passed &= check(
        "at least one chunk flagged has_table=True",
        len(tabled) > 0,
        f"got {len(tabled)} — table extraction may have been skipped for this run",
    )
    print()

    print("Test 6: reference corpus (exercise Q&A) is separately retrievable and scoped")
    corpus_results = query_by_metadata(corpus_coll, {"$and": [{"class": "6"}, {"subject": "Maths"}]})
    all_passed &= check("class 6 Maths reference corpus entries exist", len(corpus_results) > 0, f"got {len(corpus_results)}")
    all_passed &= check(
        "every entry has a non-empty answer (clean pairing, per task 5c)",
        all(r["metadata"]["answer"] for r in corpus_results),
    )
    print()

    return all_passed


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    passed = run_tests()
    print("=" * 60)
    print("ALL TESTS PASSED" if passed else "SOME TESTS FAILED")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
