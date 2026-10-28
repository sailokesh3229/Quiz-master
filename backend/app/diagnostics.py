"""Stage timings without logging credentials, question content or answers."""
import logging
from contextlib import contextmanager
from time import perf_counter

logger = logging.getLogger("uvicorn.error")


@contextmanager
def stage(name: str):
    started = perf_counter()
    logger.info("pipeline stage=%s state=start", name)
    try:
        yield
    except Exception as exc:
        logger.error("pipeline stage=%s state=failed seconds=%.2f error_type=%s",
                     name, perf_counter() - started, type(exc).__name__)
        raise
    else:
        logger.info("pipeline stage=%s state=done seconds=%.2f", name, perf_counter() - started)
