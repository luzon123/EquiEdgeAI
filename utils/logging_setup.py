"""
Logging configuration for the Poker Decision Engine.
"""
from __future__ import annotations

import contextvars
import logging
import uuid


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )


def get_logger(name: str = "poker_engine") -> logging.Logger:
    return logging.getLogger(name)


# ---------------------------------------------------------------------------
# Request-ID tracing (2026-08-21 latency investigation — instrumentation
# only, does not affect behavior). Lets one HTTP request be followed across
# module boundaries (routes/*.py -> vision/analyzer.py) in the logs without
# threading an explicit parameter through every function signature.
# ContextVar, not a plain module global: Gunicorn's threaded workers handle
# one request per thread synchronously, and contextvars are correctly
# thread-isolated, so concurrent requests on different threads never see
# each other's request_id.
# ---------------------------------------------------------------------------
_request_id_var: "contextvars.ContextVar[str]" = contextvars.ContextVar("request_id", default="-")


def new_request_id() -> str:
    """Call once at the top of a route handler. Returns and stores a short id."""
    rid = uuid.uuid4().hex[:8]
    _request_id_var.set(rid)
    return rid


def current_request_id() -> str:
    """Read the id set by new_request_id() for the in-flight request, if any."""
    return _request_id_var.get()
