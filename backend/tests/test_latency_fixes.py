import threading
import time
from types import SimpleNamespace

import pytest

from app import dedup, explanations


def test_matching_identity_uses_pairs_and_ignores_display_order():
    first = {"pairs": {"a": "x", "b": "y", "c": "z"}}
    reordered = {"pairs": {"c": "z", "a": "x", "b": "y"}}
    different = {"pairs": {"a": "y", "b": "x", "c": "z"}}
    assert dedup.matching_hash(first) == dedup.matching_hash(reordered)
    assert dedup.matching_hash(first) != dedup.matching_hash(different)
    assert dedup.question_identity_text("matching", "Match", first) != dedup.question_identity_text("matching", "Match", different)
    assert dedup.matching_hash({"pairs": {"CO": "gas"}}) != dedup.matching_hash({"pairs": {"Co": "gas"}})


class Cursor:
    def __init__(self, conn): self.conn = conn
    def __enter__(self):
        assert threading.get_ident() == self.conn.owner, "DB accessed in worker"
        return self
    def __exit__(self, *args): pass
    def execute(self, query, args): self.conn.queries.append(query)
    def fetchall(self): return self.conn.rows
    def executemany(self, query, args): self.conn.updates.extend(args)


class Connection:
    def __init__(self, rows):
        self.rows, self.queries, self.updates = rows, [], []
        self.owner = threading.get_ident()
    def cursor(self): return Cursor(self)


@pytest.mark.parametrize("count", [5, 15, 25])
def test_solution_batches_are_parallel_bounded_and_keep_db_on_caller(monkeypatch, count):
    monkeypatch.setenv("LLM_MAX_WORKERS", "4")
    rows = [dict(question_id=str(i), text="question", type="fill_in_blank", payload={"answer": "term"},
                 source_chunk_id="chunk", **{"class": "10"}, subject="Science", chapter="chapter",
                 topic="topic", explanation="cached" if i == 0 else None) for i in range(count)]
    conn = Connection(rows)
    monkeypatch.setattr(explanations, "_get_chunk_text", lambda *a: "source")
    monkeypatch.setattr(explanations, "_get_style_reference", lambda *a: None)
    active = peak = calls = 0
    lock = threading.Lock()
    def generate(*a, **kw):
        nonlocal active, peak, calls
        with lock:
            calls += 1
            active += 1
            peak = max(peak, active)
        time.sleep(0.025)
        with lock: active -= 1
        return "new explanation"
    monkeypatch.setattr(explanations, "generate_with_reliability", generate)
    result = explanations.get_explanations(conn, [str(i) for i in range(count)], None)
    assert len(result) == count and result["0"] == "cached"
    assert calls == count - 1 and len(conn.updates) == count - 1
    assert 1 < peak <= 4


def test_all_cached_solutions_skip_grounding_and_provider(monkeypatch):
    conn = Connection([dict(question_id="one", explanation="cached")])
    monkeypatch.setattr(explanations, "_get_chunk_text", lambda *a: pytest.fail("cache miss"))
    assert explanations.get_explanations(conn, ["one"], None) == {"one": "cached"}
    assert not conn.updates


def test_matching_legacy_duplicate_is_still_rejected(monkeypatch):
    payload = {"pairs": {"a": "x", "b": "y", "c": "z"}}
    conn = Connection([dict(question_id="legacy", payload=payload)])
    monkeypatch.setattr(dedup, "check_exact_duplicate", lambda *a: None)
    result = dedup.intake_question(conn, "10", "Science", "chapter", "topic", "easy", "matching",
                                   "Different instruction", payload, "chunk", [1.0])
    assert not result.accepted and result.duplicate_of == "legacy"


def test_solution_failure_does_not_persist_partial_batch(monkeypatch):
    row = dict(question_id="one", text="q", type="fill_in_blank", payload={"answer": "term"},
               source_chunk_id="chunk", **{"class": "10"}, subject="Science", chapter="chapter",
               topic="topic", explanation=None)
    conn = Connection([row])
    monkeypatch.setattr(explanations, "_get_chunk_text", lambda *a: "source")
    monkeypatch.setattr(explanations, "_get_style_reference", lambda *a: None)
    def failed(*a, **kw): raise TimeoutError("provider")
    monkeypatch.setattr(explanations, "generate_with_reliability", failed)
    with pytest.raises(TimeoutError): explanations.get_explanations(conn, ["one"], None)
    assert not conn.updates
