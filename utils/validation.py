"""
Request validation for the /decision endpoint.
"""
from __future__ import annotations

from typing import Optional

from config import (
    VALID_POSITIONS, VALID_RANKS, VALID_SUITS, RANKS,
    VALID_PLAYER_PROFILES, VALID_MODES,
    VALID_STACK_DEPTHS, VALID_FACING_ACTIONS,
)


def _validate_cards(all_cards: list) -> Optional[str]:
    """Shared card-format and duplicate check used by both validators."""
    seen: set = set()
    for raw in all_cards:
        if not isinstance(raw, str) or len(raw) != 2:
            return (
                f"Invalid card '{raw}'. Each card must be a 2-character string "
                f"(rank + suit), e.g. 'Ah', 'Kd', 'Ts'."
            )
        rank, suit = raw[0].upper(), raw[1].lower()
        if rank not in VALID_RANKS:
            return f"Invalid rank '{rank}' in card '{raw}'. Valid ranks: {', '.join(RANKS)}."
        if suit not in VALID_SUITS:
            return f"Invalid suit '{suit}' in card '{raw}'. Valid suits: s, h, d, c."
        normalized = f"{rank}{suit}"
        if normalized in seen:
            return f"Duplicate card '{raw}' detected in input."
        seen.add(normalized)
    return None


def validate_request(data: dict) -> Optional[str]:
    required = {"hand", "board", "players", "pot", "bet", "stack", "position"}
    missing  = required - data.keys()
    if missing:
        return f"Missing required field(s): {', '.join(sorted(missing))}."

    if not isinstance(data["hand"], list) or len(data["hand"]) != 2:
        return "'hand' must be a JSON array of exactly 2 card strings."

    if not isinstance(data["board"], list) or len(data["board"]) not in {0, 3, 4, 5}:
        return "'board' must be a JSON array of 0, 3, 4, or 5 card strings."

    pos = data.get("position", "")
    if not isinstance(pos, str) or pos.upper() not in VALID_POSITIONS:
        return f"'position' must be one of: {', '.join(sorted(VALID_POSITIONS))}."

    for field in ("players", "pot", "bet", "stack"):
        val = data[field]
        if not isinstance(val, (int, float)) or val < 0:
            return f"'{field}' must be a non-negative number."

    if data["players"] < 2:
        return "'players' must be at least 2."

    line_val = data.get("line", "none")
    if line_val not in ("none", "passive", "aggressive", "check_raise"):
        return "'line' must be one of: none, passive, aggressive, check_raise."

    profile_val = data.get("player_profile", "reg")
    if profile_val not in VALID_PLAYER_PROFILES:
        return f"'player_profile' must be one of: {', '.join(sorted(VALID_PLAYER_PROFILES))}."

    mode_val = data.get("mode", "full")
    if mode_val not in VALID_MODES:
        return f"'mode' must be one of: {', '.join(sorted(VALID_MODES))}."

    card_err = _validate_cards(list(data["hand"]) + list(data["board"]))
    if card_err:
        return card_err

    return None


def validate_fast_request(data: dict) -> Optional[str]:
    """Validate a fast-mode /decision request (categorical inputs only)."""
    required = {"hand", "board", "position", "stack_depth", "facing_action"}
    missing  = required - data.keys()
    if missing:
        return f"Missing required field(s) for fast mode: {', '.join(sorted(missing))}."

    if not isinstance(data["hand"], list) or len(data["hand"]) != 2:
        return "'hand' must be a JSON array of exactly 2 card strings."

    if not isinstance(data["board"], list) or len(data["board"]) not in {0, 3, 4, 5}:
        return "'board' must be a JSON array of 0, 3, 4, or 5 card strings."

    pos = data.get("position", "")
    if not isinstance(pos, str) or pos.upper() not in VALID_POSITIONS:
        return f"'position' must be one of: {', '.join(sorted(VALID_POSITIONS))}."

    if data.get("stack_depth") not in VALID_STACK_DEPTHS:
        return f"'stack_depth' must be one of: {', '.join(sorted(VALID_STACK_DEPTHS))}."

    if data.get("facing_action") not in VALID_FACING_ACTIONS:
        return f"'facing_action' must be one of: {', '.join(sorted(VALID_FACING_ACTIONS))}."

    card_err = _validate_cards(list(data["hand"]) + list(data["board"]))
    if card_err:
        return card_err

    return None
