"""
Logging configuration for the Poker Decision Engine.
"""
from __future__ import annotations

import logging


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )


def get_logger(name: str = "poker_engine") -> logging.Logger:
    return logging.getLogger(name)
