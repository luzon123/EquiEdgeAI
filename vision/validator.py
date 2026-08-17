"""
Validation layer for extracted poker game state.

Rules enforced here — the analyzer produces raw dicts, this module
cleans, normalises, and rejects impossible states before the data
reaches the poker engine.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from utils.logging_setup import get_logger

logger = get_logger()

# ---------------------------------------------------------------------------
# Card constants
# ---------------------------------------------------------------------------
_VALID_RANKS  = set("AKQJT98765432")
_VALID_SUITS  = set("shdc")
_CARD_RE      = re.compile(r"^[AKQJTakqjt2-9][shdcSHDC]$")

# Expected board card counts per street
_STREET_BOARD_COUNTS: dict[str, int] = {
    "preflop": 0,
    "flop":    3,
    "turn":    4,
    "river":   5,
}

# Confidence fields that must be 0-1 floats
_CONFIDENCE_FIELDS = [
    "hero_cards_confidence",
    "board_confidence",
    "street_confidence",
    "pot_confidence",
    "hero_stack_confidence",
    "platform_confidence",
    "hero_position_confidence",
    "dealer_confidence",
    "overall_confidence",
]


# ---------------------------------------------------------------------------
# Validation result
# ---------------------------------------------------------------------------
@dataclass
class ValidationResult:
    valid:    bool
    warnings: list[str] = field(default_factory=list)
    errors:   list[str] = field(default_factory=list)
    data:     dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def validate_game_state(raw: dict[str, Any]) -> ValidationResult:
    """
    Validate and normalise raw game state extracted by the vision model.

    Accepts both the legacy field names (hero_cards, board, pot, …) and the
    ClubGG-specific names returned by the Claude prompt (total_pot,
    active_players, my_hand, board_cards, my_stack, my_position,
    action_required).  The ClubGG names are translated to internal names
    before validation runs so the rest of the pipeline is unchanged.

    Returns a ValidationResult.  If `valid` is False the data should not
    be forwarded to the poker engine.
    """
    result   = ValidationResult(valid=True, data=raw.copy())
    warnings = result.warnings
    errors   = result.errors

    _translate_clubgg_fields(result.data, warnings)
    _validate_hero_cards(result.data, errors, warnings)
    _validate_board(result.data, errors, warnings)
    _validate_street_board_consistency(result.data, warnings)
    _validate_numeric_fields(result.data, errors, warnings)
    _validate_seat_numbers(result.data, warnings)
    _validate_confidence_fields(result.data, warnings)
    _validate_players(result.data, errors, warnings)
    _validate_no_duplicate_cards(result.data, errors)

    if errors:
        result.valid = False

    if warnings:
        logger.warning("Vision validation warnings: %s", "; ".join(warnings))
    if errors:
        logger.error("Vision validation errors: %s", "; ".join(errors))

    return result


# ---------------------------------------------------------------------------
# ClubGG field translator
# ---------------------------------------------------------------------------
def _translate_clubgg_fields(data: dict, warnings: list) -> None:
    """
    Map Claude/ClubGG prompt field names to the internal canonical names
    used by VisionGameState.  Only translates fields that are absent under
    the canonical name so legacy responses continue to work unchanged.
    """
    # total_pot → pot
    if "pot" not in data and "total_pot" in data:
        data["pot"] = data.pop("total_pot")

    # active_players (int) → player_count
    if "player_count" not in data and "active_players" in data:
        data["player_count"] = data.pop("active_players")

    # my_hand → hero_cards
    if "hero_cards" not in data and "my_hand" in data:
        data["hero_cards"] = data.pop("my_hand")

    # board_cards → board
    if "board" not in data and "board_cards" in data:
        data["board"] = data.pop("board_cards")

    # my_stack → hero_stack
    if "hero_stack" not in data and "my_stack" in data:
        data["hero_stack"] = data.pop("my_stack")

    # my_position → hero_position (informational; not used by VisionGameState directly)
    if "hero_position" not in data and "my_position" in data:
        data["hero_position"] = data.pop("my_position")

    # action_required: {"type": "call"|"check", "amount": X} → call_amount
    if "call_amount" not in data and isinstance(data.get("action_required"), dict):
        action = data.pop("action_required")
        action_type = (action.get("type") or "").lower()
        amount = action.get("amount")
        if action_type == "call" and isinstance(amount, (int, float)):
            data["call_amount"] = amount
        elif action_type in ("check", "check/fold"):
            data["call_amount"] = 0


# ---------------------------------------------------------------------------
# Individual validators
# ---------------------------------------------------------------------------
def _normalise_card(card: Any) -> str | None:
    """Normalise a card string to lowercase rank + lowercase suit. Returns None if invalid."""
    if not isinstance(card, str) or len(card) != 2:
        return None
    rank = card[0].upper()
    suit = card[1].lower()
    if rank not in _VALID_RANKS or suit not in _VALID_SUITS:
        return None
    return rank + suit


def _validate_hero_cards(raw: dict, errors: list, warnings: list) -> None:
    hero = raw.get("hero_cards")
    if hero is None:
        warnings.append("hero_cards is null — could not identify hero hand.")
        return

    if not isinstance(hero, list):
        errors.append(f"hero_cards must be a list, got {type(hero).__name__}.")
        return

    if len(hero) != 2:
        errors.append(f"hero_cards must contain exactly 2 cards, got {len(hero)}.")
        return

    normalised = []
    for c in hero:
        nc = _normalise_card(c)
        if nc is None:
            errors.append(f"Invalid card in hero_cards: {c!r}.")
        else:
            normalised.append(nc)

    if len(normalised) == 2:
        raw["hero_cards"] = normalised


def _validate_board(raw: dict, errors: list, warnings: list) -> None:
    board = raw.get("board")
    if board is None:
        raw["board"] = []
        warnings.append("board is null — defaulting to empty list.")
        return

    if not isinstance(board, list):
        errors.append(f"board must be a list, got {type(board).__name__}.")
        return

    if len(board) not in (0, 3, 4, 5):
        errors.append(f"board has {len(board)} cards — must be 0, 3, 4, or 5.")
        return

    normalised = []
    for c in board:
        nc = _normalise_card(c)
        if nc is None:
            errors.append(f"Invalid card on board: {c!r}.")
        else:
            normalised.append(nc)

    if len(normalised) == len(board):
        raw["board"] = normalised


def _validate_street_board_consistency(raw: dict, warnings: list) -> None:
    street = raw.get("street")
    board  = raw.get("board") or []

    if street is None:
        return

    street_lower = street.lower() if isinstance(street, str) else ""
    raw["street"] = street_lower

    expected = _STREET_BOARD_COUNTS.get(street_lower)
    if expected is None:
        warnings.append(f"Unknown street value: {street!r}.")
        return

    if len(board) != expected:
        warnings.append(
            f"Street is {street_lower!r} but board has {len(board)} cards "
            f"(expected {expected})."
        )


def _validate_numeric_fields(raw: dict, errors: list, warnings: list) -> None:
    numeric_fields = [
        "pot", "call_amount", "current_bet", "hero_stack",
        "small_blind_amount", "big_blind_amount",
    ]
    for f_name in numeric_fields:
        val = raw.get(f_name)
        if val is None:
            continue
        if not isinstance(val, (int, float)):
            errors.append(f"{f_name} must be a number, got {type(val).__name__}: {val!r}.")
            continue
        if val < 0:
            errors.append(f"{f_name} cannot be negative: {val}.")


def _validate_seat_numbers(raw: dict, warnings: list) -> None:
    seat_fields = [
        "dealer_position", "small_blind_position", "big_blind_position",
        "hero_seat", "acting_seat",
    ]
    for f_name in seat_fields:
        val = raw.get(f_name)
        if val is None:
            continue
        if not isinstance(val, int) or not (1 <= val <= 10):
            warnings.append(f"{f_name} seat number out of range (1-10): {val!r}.")


def _validate_confidence_fields(raw: dict, warnings: list) -> None:
    for f_name in _CONFIDENCE_FIELDS:
        val = raw.get(f_name)
        if val is None:
            raw[f_name] = 0.0
            continue
        if not isinstance(val, (int, float)):
            warnings.append(f"{f_name} must be a float, got {type(val).__name__}.")
            raw[f_name] = 0.0
            continue
        if not (0.0 <= float(val) <= 1.0):
            warnings.append(f"{f_name} out of range [0,1]: {val}.")
            raw[f_name] = max(0.0, min(1.0, float(val)))


def _validate_players(raw: dict, errors: list, warnings: list) -> None:
    players = raw.get("players")
    if not isinstance(players, list):
        raw["players"] = []
        if players is not None:
            errors.append(f"players must be a list, got {type(players).__name__}.")
        return

    valid_statuses = {"active", "folded", "all_in", "sitting_out", "empty"}
    seen_seats: set[int] = set()

    for i, p in enumerate(players):
        if not isinstance(p, dict):
            errors.append(f"players[{i}] must be a dict.")
            continue

        seat = p.get("seat")
        if not isinstance(seat, int) or not (1 <= seat <= 10):
            warnings.append(f"players[{i}] has invalid seat: {seat!r}.")
        elif seat in seen_seats:
            warnings.append(f"Duplicate seat number {seat} in players list.")
        else:
            seen_seats.add(seat)

        status = p.get("status")
        if status not in valid_statuses:
            warnings.append(f"players[{i}] has unknown status: {status!r}.")

        stack = p.get("stack")
        if stack is not None and not isinstance(stack, (int, float)):
            warnings.append(f"players[{i}] stack must be a number: {stack!r}.")

        conf = p.get("stack_confidence")
        if conf is not None and not isinstance(conf, (int, float)):
            p["stack_confidence"] = 0.0


def _validate_no_duplicate_cards(raw: dict, errors: list) -> None:
    seen: set[str] = set()
    all_cards: list[str] = []

    hero  = raw.get("hero_cards") or []
    board = raw.get("board") or []

    for c in hero + board:
        if isinstance(c, str):
            all_cards.append(c.upper())

    for card in all_cards:
        if card in seen:
            errors.append(f"Duplicate card detected: {card}.")
        seen.add(card)
