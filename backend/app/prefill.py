"""Prepare reviewable bank candidates off the student request path.

Run from backend: python -m app.prefill --help. Generation writes a draft;
only candidates explicitly marked approved=true can be imported later.
Neither mode consumes student quiz quota or marks questions seen.
"""
import argparse
import hashlib
import json
from pathlib import Path

from app.background import generate_parallel
from app.generation import get_grounding_chunks, _generate_one_validated_question
from app.source_quality import REPAIRED_CHUNK_IDS, SOURCE_REVISION
from app.prompts import parse_and_validate


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def validate_candidate(candidate, scope, chunks):
    if candidate.get("approved") is not True:
        raise ValueError("candidate has not been approved")
    if candidate["difficulty"] not in ("easy", "medium", "hard"):
        raise ValueError("invalid difficulty")
    chunk = chunks.get(candidate["chunk_id"])
    if chunk is None or digest(chunk["text"]) != candidate["source_sha256"]:
        raise ValueError("source changed or is outside the requested scope; review again")
    payload = parse_and_validate(candidate["type"], json.dumps(candidate["payload"]))
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--class", dest="class_", required=True)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--chapter", required=True)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--per-cell", type=int, default=1, help="1-5 candidates per difficulty/type (9 cells)")
    parser.add_argument("--import-reviewed", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.per_cell <= 5:
        parser.error("--per-cell must be 1-5")
    scope = {k: getattr(args, k) for k in ("class_", "subject", "chapter", "topic")}
    chunks = get_grounding_chunks(args.class_, args.subject, args.topic, 5, args.chapter)
    if not chunks:
        raise ValueError("no source chunks for this scope")
    by_id = {c["chunk_id"]: c for c in chunks}
    if args.import_reviewed:
        from app.db import get_connection
        from app.deps import get_embed_model
        from app.dedup import intake_question, question_identity_text
        draft = json.loads(args.file.read_text(encoding="utf-8"))
        if draft["scope"] != scope:
            raise ValueError("draft scope does not match command")
        selected = [c for c in draft["candidates"] if c.get("approved") is True]
        validated = [(c, validate_candidate(c, scope, by_id)) for c in selected]
        if not validated:
            raise ValueError("no approved candidates")
        model = get_embed_model()
        accepted = 0
        with get_connection() as conn:
            for candidate, payload in validated:
                data = payload.model_dump()
                if candidate["chunk_id"] in REPAIRED_CHUNK_IDS:
                    data["source_revision"] = SOURCE_REVISION
                result = intake_question(conn, class_=args.class_, subject=args.subject,
                    chapter=args.chapter, topic=args.topic, difficulty=candidate["difficulty"],
                    question_type=candidate["type"], text=payload.question, payload=data,
                    source_chunk_id=candidate["chunk_id"],
                    embedding=model.encode(question_identity_text(candidate["type"], payload.question, data)).tolist())
                accepted += int(result.accepted)
        print(f"Imported {accepted}/{len(selected)} reviewed candidates; duplicates skipped.")
        return
    if args.file.exists():
        raise ValueError("draft already exists; choose a new file")
    from app.deps import get_primary_provider, get_fallback_provider
    primary, fallback = get_primary_provider(), get_fallback_provider()
    jobs = [(difficulty, qtype, chunks[i % len(chunks)])
            for difficulty in ("easy", "medium", "hard")
            for qtype in ("MCQ", "fill_in_blank", "matching") for i in range(args.per_cell)]
    def generate(job):
        difficulty, qtype, chunk = job
        result = _generate_one_validated_question(chunk, qtype, difficulty, primary, fallback, 2)
        if result is None:
            raise ValueError("candidate validation exhausted")
        return dict(type=qtype, difficulty=difficulty, chunk_id=chunk["chunk_id"],
                    source_sha256=digest(chunk["text"]), source_text=chunk["text"],
                    payload=result["payload"].model_dump(), approved=False)
    outcome = generate_parallel(jobs, generate)
    args.file.write_text(json.dumps(dict(scope=scope,
        candidates=[outcome.results[i] for i in sorted(outcome.results)],
        failed_cells=[dict(difficulty=jobs[i][0], type=jobs[i][1]) for i in outcome.errors]),
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Prepared {len(outcome.results)} candidates; {len(outcome.errors)} failed. Review source and answers before approval.")


if __name__ == "__main__":
    main()
