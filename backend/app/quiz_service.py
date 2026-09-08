"""Section 3: wires the web app's quiz-selection flow to Section 2's
engine (PLAN.md section 10). Closes the documented multi-chapter gap in
`quiz_engine.assemble_quiz` by resolving scope ONCE over a flat topic list
spanning every selected chapter (reusing `scope_resolution.resolve_scope`
exactly as Section 2 built it), then looping `bank_lookup`/`generate_fn`
per allocation line with that line's own chapter — the one piece
`assemble_quiz` itself can't do (PLAN.md section 10).
"""

from __future__ import annotations

import dataclasses
from collections import defaultdict
from dataclasses import dataclass, field

import psycopg

from app.bank_lookup import lookup_bank
from app.catalog import CatalogError, list_chapters, list_topics, validate_scope
from app.config import DAILY_QUIZ_GENERATION_LIMIT
from app.explanations import get_or_generate_explanation
from app.generation import make_generate_fn
from app.grading import grade_answer, grade_matching
from app.llm_client import Provider
from app.quiz_engine import mark_seen
from app.scope_resolution import QuizRequest, resolve_scope
from app.scoring import QuizSubmission, submit_quiz


class DailyLimitExceeded(Exception):
    pass


class QuizCreationError(Exception):
    """Generation failed even after retries/fallback (SPEC section 9) —
    surfaced as the 'Generation failed' screen: retry, or ask for a
    smaller quiz assembled from the bank only (allow_partial=True)."""


@dataclass
class CreateQuizRequest:
    user_id: str
    class_: str
    subject: str
    scope_type: str  # "single_topic" | "multi_topic" | "all_topics_in_chapter" | "all_chapters_in_subject"
    chapters: list[str]  # selected chapter(s); for all_chapters_in_subject, every chapter in the subject
    topics: list[str] | None  # specific topics within `chapters`; None/empty = "all topics" in each selected chapter
    difficulty: str
    question_count: int
    question_types: list[str]
    allow_partial: bool = False  # "smaller quiz from the bank" retry path (SPEC 9's generation-failure UX)


@dataclass
class DeliveredQuestion:
    question_id: str
    question_type: str
    topic: str
    chapter: str
    difficulty: str
    source: str  # "bank" | "generated"
    text: str
    public_payload: dict  # correct answer stripped — see _public_payload


@dataclass
class CreateQuizResult:
    questions: list[DeliveredQuestion]
    requested_count: int
    partial: bool


def get_daily_usage(conn: psycopg.Connection, user_id: str) -> tuple[int, int]:
    """(used_today, limit) — SPEC section 15 / design screens 3, 12, 14
    all surface this count directly to the user."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM quiz_generation_log WHERE user_id = %s AND created_at >= now() - interval '1 day'",
            (user_id,),
        )
        return cur.fetchone()["n"], DAILY_QUIZ_GENERATION_LIMIT


def check_and_log_daily_limit(conn: psycopg.Connection, user_id: str) -> None:
    count, limit = get_daily_usage(conn, user_id)
    if count >= limit:
        raise DailyLimitExceeded(f"daily quiz generation limit ({limit}) reached")
    with conn.cursor() as cur:
        cur.execute("INSERT INTO quiz_generation_log (user_id) VALUES (%s)", (user_id,))


def _resolve_sub_units(request: CreateQuizRequest) -> tuple[list[str], dict[str, tuple[str, str]]]:
    """Flat list of unique sub-unit KEYS across every selected chapter, plus
    a key -> (chapter, topic) map (PLAN.md section 10). Coverage rule /
    accuracy-weighting then run ONCE over this flat pool via resolve_scope,
    matching SPEC 10a's own math instead of approximating it with a nested
    split.

    The key is a synthetic per-(chapter, topic) token, NOT the topic name
    itself: topic names collide across chapters in the real corpus (e.g.
    "Introduction" is a real topic name shared by 255 different chapters,
    found auditing classes 11-12) -- keying sub-units on the bare topic
    name would silently collapse two selected chapters' same-named topics
    into one allocation line, under-covering one of them (breaking SPEC
    10a's coverage guarantee for exactly the "all chapters"/subject-wide
    requests where this collision is common) and, before this fix,
    corrupting cross-request accuracy-weighting the same way. resolve_scope
    only ever treats a sub_unit as an opaque grouping identifier, so it
    doesn't care that the key isn't a display name -- callers below recover
    the real chapter/topic pair via the returned map wherever a real name
    is actually needed (bank lookup, generation, the delivered question's
    displayed topic)."""
    all_topics_requested = request.scope_type in ("all_topics_in_chapter", "all_chapters_in_subject") or not request.topics

    sub_unit_keys: list[str] = []
    sub_unit_key_map: dict[str, tuple[str, str]] = {}
    for chapter in request.chapters:
        chapter_topics = list_topics(request.class_, request.subject, chapter)
        names = [t.topic for t in chapter_topics]
        chosen = names if all_topics_requested else [t for t in names if t in (request.topics or [])]
        for t in chosen:
            key = f"su{len(sub_unit_keys)}"
            sub_unit_keys.append(key)
            sub_unit_key_map[key] = (chapter, t)
    return sub_unit_keys, sub_unit_key_map


def _fetch_performance_history(
    conn: psycopg.Connection, user_id: str, class_: str, subject: str, sub_unit_key_map: dict[str, tuple[str, str]]
) -> dict[tuple[str, str], tuple[int, int]]:
    """Returns {(sub_unit_key, question_type): (correct, total)}, scoped by
    (chapter, topic) pair rather than topic name alone -- see
    _resolve_sub_units for why a bare topic name isn't a safe lookup key."""
    if not sub_unit_key_map:
        return {}
    chapters = list({chapter for chapter, _ in sub_unit_key_map.values()})
    with conn.cursor() as cur:
        cur.execute(
            "SELECT chapter, topic, question_type, correct_count, total_count FROM performance_counters "
            "WHERE user_id = %s AND class = %s AND subject = %s AND chapter = ANY(%s)",
            (user_id, class_, subject, chapters),
        )
        rows = cur.fetchall()
    by_chapter_topic: dict[tuple[str, str], dict[str, tuple[int, int]]] = defaultdict(dict)
    for r in rows:
        by_chapter_topic[(r["chapter"], r["topic"])][r["question_type"]] = (r["correct_count"], r["total_count"])

    history: dict[tuple[str, str], tuple[int, int]] = {}
    for key, chapter_topic in sub_unit_key_map.items():
        for qtype, counts in by_chapter_topic.get(chapter_topic, {}).items():
            history[(key, qtype)] = counts
    return history


def _public_payload(question_type: str, payload: dict) -> dict:
    """Strips the correct-answer field(s) so the client never receives
    them before submission (SPEC 8a: solutions are a deliberate, separate
    reveal, not bundled into the delivered quiz)."""
    if question_type == "MCQ":
        return {"options": payload["options"]}
    if question_type == "fill_in_blank":
        return {}  # question text alone carries the blank
    if question_type == "matching":
        return {"left_items": payload["left_items"], "right_items": payload["right_items"]}
    raise ValueError(f"unknown question_type: {question_type}")


def create_quiz(
    conn: psycopg.Connection,
    request: CreateQuizRequest,
    provider: Provider,
    fallback_provider: Provider | None,
    embed_model,
) -> CreateQuizResult:
    try:
        validate_scope(request.class_, request.subject, request.chapters, request.topics)
    except CatalogError as e:
        raise ValueError(str(e)) from e

    sub_unit_keys, sub_unit_key_map = _resolve_sub_units(request)
    if not sub_unit_keys:
        raise ValueError("resolved scope has no topics — check chapter/topic selection")

    performance_history = _fetch_performance_history(conn, request.user_id, request.class_, request.subject, sub_unit_key_map)

    scope_request = QuizRequest(
        class_=request.class_,
        subject=request.subject,
        scope_type=request.scope_type,
        sub_units=sub_unit_keys,
        difficulty=request.difficulty,
        question_count=request.question_count,
        question_types=request.question_types,
    )
    allocations = resolve_scope(scope_request, performance_history=performance_history or None)

    generate_fns: dict[str, callable] = {}

    def _generate_fn_for(chapter: str):
        if chapter not in generate_fns:
            generate_fns[chapter] = make_generate_fn(
                conn, class_=request.class_, subject=request.subject, chapter=chapter,
                provider=provider, fallback_provider=fallback_provider, embed_model=embed_model,
            )
        return generate_fns[chapter]

    assembled_ids: list[str] = []
    assembled_meta: list[tuple[str, str, str, str, str]] = []  # (question_id, type, topic, chapter, source)
    used_cluster_ids: list[str] = []
    partial = False

    for line in allocations:
        chapter, topic = sub_unit_key_map[line.sub_unit]
        bank_result = lookup_bank(
            conn, class_=request.class_, subject=request.subject, chapter=chapter, topic=topic,
            difficulty=line.difficulty, question_type=line.question_type, user_id=request.user_id,
            count=line.count, exclude_cluster_ids=used_cluster_ids,
        )
        used_cluster_ids.extend(bank_result.cluster_ids)
        for qid in bank_result.question_ids:
            assembled_ids.append(qid)
            assembled_meta.append((qid, line.question_type, topic, chapter, "bank"))

        if bank_result.shortfall > 0:
            if request.allow_partial:
                partial = True
                continue
            # generate_fn (make_generate_fn) uses line.sub_unit as the real
            # topic name for chunk retrieval and bank intake — swap the
            # opaque allocation key for the real topic before calling it.
            real_line = dataclasses.replace(line, sub_unit=topic)
            try:
                generated = _generate_fn_for(chapter)(real_line, bank_result.shortfall)
            except Exception as e:  # noqa: BLE001 — ReliabilityError or any generation-path failure
                raise QuizCreationError(f"generation failed for {topic}/{line.question_type}: {e}") from e
            if len(generated) < bank_result.shortfall:
                raise QuizCreationError(
                    f"could only generate {len(generated)}/{bank_result.shortfall} for "
                    f"{topic}/{line.question_type} — topic may be content-starved"
                )
            for g in generated:
                assembled_ids.append(g["question_id"])
                assembled_meta.append((g["question_id"], line.question_type, topic, chapter, "generated"))

    if not assembled_ids:
        raise QuizCreationError("no questions could be assembled for this scope")

    mark_seen(conn, request.user_id, assembled_ids)

    with conn.cursor() as cur:
        cur.execute("SELECT question_id, text, difficulty, payload FROM questions WHERE question_id = ANY(%s)", (assembled_ids,))
        rows = {str(r["question_id"]): r for r in cur.fetchall()}

    questions = []
    for qid, qtype, topic, chapter, source in assembled_meta:
        row = rows[qid]
        questions.append(
            DeliveredQuestion(
                question_id=qid, question_type=qtype, topic=topic, chapter=chapter, difficulty=row["difficulty"],
                source=source, text=row["text"], public_payload=_public_payload(qtype, row["payload"]),
            )
        )

    return CreateQuizResult(questions=questions, requested_count=request.question_count, partial=partial)


@dataclass
class SubmitQuizRequest:
    user_id: str
    class_: str
    subject: str
    scope: dict  # {"scope_type": ..., "sub_units": [...]}
    difficulty: str
    is_requiz: bool
    answers: dict[str, object]  # question_id -> the user's raw answer


@dataclass
class SubmitQuizResultView:
    attempt_id: str | None
    score_correct: int
    score_total: int
    options: tuple[str, ...]
    per_question: list[dict] = field(default_factory=list)  # no correct-answer data — see SPEC 8a


def submit_quiz_request(conn: psycopg.Connection, request: SubmitQuizRequest, embed_model) -> SubmitQuizResultView:
    question_ids = list(request.answers.keys())
    with conn.cursor() as cur:
        cur.execute("SELECT question_id, type, topic, chapter, payload FROM questions WHERE question_id = ANY(%s)", (question_ids,))
        rows = {str(r["question_id"]): r for r in cur.fetchall()}

    graded = []
    for qid, user_answer in request.answers.items():
        row = rows.get(qid)
        if row is None:
            raise ValueError(f"unknown question_id: {qid}")
        graded.append(grade_answer(row["type"], qid, row["topic"], row["chapter"], row["payload"], user_answer, embed_model=embed_model))

    submission = QuizSubmission(
        user_id=request.user_id, class_=request.class_, subject=request.subject, scope=request.scope,
        difficulty=request.difficulty, is_requiz=request.is_requiz, answers=graded, user_answer_payloads=request.answers,
    )
    result = submit_quiz(conn, submission)

    per_question = []
    for a in graded:
        entry = {"question_id": a.question_id, "question_type": a.question_type, "topic": a.topic, "is_correct": a.is_correct}
        if a.detail is not None:
            entry["row_results"] = a.detail.row_results  # SPEC section 8: per-row green/red for matching
        per_question.append(entry)

    return SubmitQuizResultView(
        attempt_id=result.attempt_id, score_correct=result.score_correct, score_total=result.score_total,
        options=result.options, per_question=per_question,
    )


def _build_solution_entry(
    conn: psycopg.Connection, question_id: str, qtype: str, topic: str, text: str, payload: dict,
    user_answer, is_correct: bool, provider: Provider, fallback: Provider | None, row_results: list[bool] | None,
) -> dict:
    """SPEC 8a: reopens the question read-only with the user's answer,
    green/red marking, and a source-grounded explanation below it."""
    explanation = get_or_generate_explanation(conn, question_id, provider, fallback)
    entry = {
        "question_id": question_id, "question_type": qtype, "topic": topic, "text": text,
        "correct_payload": payload, "user_answer": user_answer, "is_correct": is_correct, "explanation": explanation,
    }
    if qtype == "matching":
        entry["row_results"] = row_results if row_results is not None else grade_matching(payload, user_answer).row_results
    return entry


def solutions_from_attempt(conn: psycopg.Connection, attempt_id: str, provider: Provider, fallback: Provider | None) -> list[dict]:
    """SPEC 8a: 'available any time after the quiz is submitted' — reads
    the persisted attempt rather than re-grading, so a real (non-requiz)
    attempt's solutions survive a page refresh or a later visit."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT aa.question_id, aa.user_answer, aa.is_correct, q.type, q.topic, q.text, q.payload
            FROM attempt_answers aa JOIN questions q ON q.question_id = aa.question_id
            WHERE aa.attempt_id = %s
            """,
            (attempt_id,),
        )
        rows = cur.fetchall()
    if not rows:
        raise ValueError(f"unknown attempt_id: {attempt_id}")
    return [
        _build_solution_entry(
            conn, str(r["question_id"]), r["type"], r["topic"], r["text"], r["payload"],
            r["user_answer"], r["is_correct"], provider, fallback, row_results=None,
        )
        for r in rows
    ]


def solutions_from_answers(
    conn: psycopg.Connection, answers: dict[str, object], provider: Provider, fallback: Provider | None, embed_model
) -> list[dict]:
    """A requiz replay never writes an attempt (SPEC section 11), so its
    solutions have nothing persisted to read back — graded fresh from the
    question_ids/answers the client already holds from just taking it."""
    question_ids = list(answers.keys())
    with conn.cursor() as cur:
        cur.execute("SELECT question_id, type, topic, chapter, text, payload FROM questions WHERE question_id = ANY(%s)", (question_ids,))
        rows = {str(r["question_id"]): r for r in cur.fetchall()}

    entries = []
    for qid, user_answer in answers.items():
        row = rows[qid]
        graded = grade_answer(row["type"], qid, row["topic"], row["chapter"], row["payload"], user_answer, embed_model=embed_model)
        row_results = graded.detail.row_results if graded.detail is not None else None
        entries.append(
            _build_solution_entry(
                conn, qid, row["type"], row["topic"], row["text"], row["payload"],
                user_answer, graded.is_correct, provider, fallback, row_results=row_results,
            )
        )
    return entries
