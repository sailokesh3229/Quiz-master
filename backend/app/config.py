"""Loads backend/.env into the process environment once, on first import."""

import os
from pathlib import Path

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
_loaded = False


def load_dotenv() -> None:
    global _loaded
    if _loaded or not _ENV_PATH.exists():
        return
    for line in _ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())
    _loaded = True


load_dotenv()

# SPEC section 15: soft daily cap on quiz generations per user, purely to
# control LLM cost during early testing/development. A config value (not
# hardcoded in the endpoint), adjustable via env without a code change.
DAILY_QUIZ_GENERATION_LIMIT = int(os.environ.get("DAILY_QUIZ_GENERATION_LIMIT", "10"))

# PLAN.md section 2, section 8: overgeneration factor for background bank
# pre-filling. Kept small by default per the "budget caution" resolution —
# dial up once the core flow is tested on a larger LLM budget.
OVERGENERATION_FACTOR = float(os.environ.get("OVERGENERATION_FACTOR", "1.0"))
OVERGENERATION_CAP = int(os.environ.get("OVERGENERATION_CAP", "5"))
