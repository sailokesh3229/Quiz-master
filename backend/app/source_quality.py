"""Versioned corrections from PDF glyph positions, never guessed formula edits.

The manifest refers to class 10 Science, Chemical Reactions and Equations.
Re-ingesting the corpus eventually replaces this compatibility layer.
"""
import json
import re
from pathlib import Path

SOURCE_REVISION = "chemistry-subscripts-v1"
_ROOT = Path(__file__).parent
REPAIRS = json.loads((_ROOT / "source_repairs.json").read_text(encoding="utf-8"))
_IDS = _ROOT / "repaired_chunk_ids.json"
REPAIRED_CHUNK_IDS = frozenset(json.loads(_IDS.read_text()) if _IDS.exists() else [])
REVIEW_NOTICE = "This question used textbook text with damaged chemical subscripts. Its saved score is unchanged; this question is excluded from new quizzes pending review."


def repair_source(text: str) -> str:
    for patch in REPAIRS:
        text = text.replace(patch["before"], patch["after"])
        # The corpus cleaning step strips printed equation numbers.
        before = re.sub(r"\(\d+\.\d+\)", "", patch["before"])
        after = re.sub(r"\(\d+\.\d+\)", "", patch["after"])
        text = text.replace(before, after)
    return text


def needs_review(row: dict) -> bool:
    return (row.get("source_chunk_id") in REPAIRED_CHUNK_IDS
            and row.get("payload", {}).get("source_revision") != SOURCE_REVISION)
