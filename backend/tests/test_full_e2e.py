"""Task 13: full end-to-end test — one complete quiz request through
delivery, submission, scoring, and history update, on real data from
Section 1's chunking output. Hits the real OpenAI API for generation
(the bank starts empty inside the test transaction, so every question
for this scope must be freshly generated) — costs a small amount of real
usage per run.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.generation import make_generate_fn
from app.grading import grade_answer
from app.llm_client import OpenAIProvider
from app.quiz_engine import assemble_quiz
from app.scope_resolution import QuizRequest
from app.scoring import QuizSubmission, submit_quiz

CLASS = "10"
SUBJECT = "Science"
CHAPTER = "Chemical Reactions and Equations"
TOPIC = "CHEMICAL EQUATIONS"  # Section 1's stored topic name (task 6 finding: often ALL-CAPS from the source PDF)


def test_full_quiz_lifecycle_on_real_data(db_conn, embed_model):
    provider = OpenAIProvider()
    generate_fn = make_generate_fn(
        db_conn, class_=CLASS, subject=SUBJECT, chapter=CHAPTER,
        provider=provider, fallback_provider=None, embed_model=embed_model,
    )

    request = QuizRequest(
        class_=CLASS, subject=SUBJECT, scope_type="single_topic",
        sub_units=[TOPIC], difficulty="medium", question_count=5, question_types=["MCQ"],
    )

    # --- Steps A-D: assemble the quiz (bank empty in this test transaction -> full generation) ---
    assembled = assemble_quiz(db_conn, request, chapter=CHAPTER, user_id="e2e_user", generate_fn=generate_fn)

    assert len(assembled) == 5, "SPEC section 12: exact requested count must always be delivered"
    assert all(q.source == "generated" for q in assembled), "bank was empty in this scope, everything should be freshly generated"
    assert all(q.question_type == "MCQ" for q in assembled)
    assert all(q.topic == TOPIC for q in assembled)

    # --- fetch the full question records (payload) to simulate the user answering ---
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT question_id, payload FROM questions WHERE question_id = ANY(%s)",
            ([q.question_id for q in assembled],),
        )
        payloads = {str(row["question_id"]): row["payload"] for row in cur.fetchall()}

    assert len(payloads) == 5
    for qid, payload in payloads.items():
        assert "correct_option_index" in payload
        assert len(payload["options"]) == 4

    # --- simulate the user answering: get the FIRST question right, rest wrong ---
    graded_answers = []
    user_answer_payloads = {}
    for i, q in enumerate(assembled):
        payload = payloads[q.question_id]
        correct_index = payload["correct_option_index"]
        user_answer = correct_index if i == 0 else (correct_index + 1) % 4
        user_answer_payloads[q.question_id] = user_answer
        graded_answers.append(grade_answer("MCQ", q.question_id, q.topic, CHAPTER, payload, user_answer))

    assert sum(1 for a in graded_answers if a.is_correct) == 1, "exactly one answer (the first) was set to correct"

    # --- Step E: score, record, four options ---
    submission = QuizSubmission(
        user_id="e2e_user", class_=CLASS, subject=SUBJECT,
        scope={"scope_type": "single_topic", "sub_units": [TOPIC]}, difficulty="medium",
        is_requiz=False, answers=graded_answers, user_answer_payloads=user_answer_payloads,
    )
    result = submit_quiz(db_conn, submission)

    assert result.attempt_id is not None
    assert result.score_correct == 1
    assert result.score_total == 5

    # --- verify persistence: quiz_attempts, attempt_answers, performance_counters, source_chunk_id traceability ---
    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM attempt_answers WHERE attempt_id = %s", (result.attempt_id,))
        assert cur.fetchone()["n"] == 5

        cur.execute(
            "SELECT correct_count, total_count FROM performance_counters "
            "WHERE user_id = 'e2e_user' AND class = %s AND subject = %s AND chapter = %s AND topic = %s AND question_type = 'MCQ'",
            (CLASS, SUBJECT, CHAPTER, TOPIC),
        )
        row = cur.fetchone()
        assert (row["correct_count"], row["total_count"]) == (1, 5)

        # SPEC section 6: every question traceable back to its source chunk
        cur.execute("SELECT source_chunk_id FROM questions WHERE question_id = ANY(%s)", ([q.question_id for q in assembled],))
        chunk_ids = [r["source_chunk_id"] for r in cur.fetchall()]
        assert all(chunk_ids), "every generated question must carry a source_chunk_id"

    # --- a retake for the same user now excludes these 5 (all seen) -> must generate 5 fresh ones ---
    request2 = QuizRequest(
        class_=CLASS, subject=SUBJECT, scope_type="single_topic",
        sub_units=[TOPIC], difficulty="medium", question_count=5, question_types=["MCQ"],
    )
    second_assembled = assemble_quiz(db_conn, request2, chapter=CHAPTER, user_id="e2e_user", generate_fn=generate_fn)
    assert len(second_assembled) == 5
    first_ids = {q.question_id for q in assembled}
    second_ids = {q.question_id for q in second_assembled}
    assert first_ids.isdisjoint(second_ids), "retake must serve genuinely new-to-this-user questions"
