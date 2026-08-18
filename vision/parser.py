"""
JSON parser for raw vision-model responses.

Responsibilities:
- Strip any accidental markdown fences.
- Parse JSON safely.
- Return the raw dict for the validator to check.
- Never perform validation logic here.
"""
from __future__ import annotations

import json
import re
from typing import Any

from utils.logging_setup import get_logger

logger = get_logger()

# Regex to strip ```json ... ``` or ``` ... ``` fences the model may emit
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def extract_json_from_text(text: str) -> dict[str, Any]:
    """
    Parse the model's text output into a dict.

    Tries:
    1. Direct JSON parse.
    2. Strip markdown code fences, then parse.
    3. Find the first {...} block and parse it.

    Raises:
        ValueError: if no valid JSON object could be extracted.
    """
    text = text.strip()

    # Attempt 1 — direct parse
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # Attempt 2 — strip markdown fences
    match = _FENCE_RE.search(text)
    if match:
        try:
            result = json.loads(match.group(1))
            if isinstance(result, dict):
                logger.debug("Parsed JSON after stripping markdown fences.")
                return result
        except json.JSONDecodeError:
            pass

    # Attempt 3 — find outermost { ... }
    start = text.find("{")
    end   = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            result = json.loads(text[start : end + 1])
            if isinstance(result, dict):
                logger.debug("Parsed JSON by extracting first {...} block.")
                return result
        except json.JSONDecodeError:
            pass

    raise ValueError(
        f"Could not extract a JSON object from model response. "
        f"Response preview: {text[:200]!r}"
    )
