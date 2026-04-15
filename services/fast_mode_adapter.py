"""
Fast mode adapter.

Maps categorical fast-mode inputs (stack_depth, facing_action) to the
numeric engine parameters expected by the existing poker engine.

Design intent
─────────────
Fast mode avoids numeric forms. The user selects a stack depth bucket and
the facing-action type; this module translates those choices into concrete
chip values so the shared engine can run without modification.

Base unit: 1 chip ≈ 1 BB (big blind).
Synthetic pot: a standard 2-player 3bb-open + call = 6bb pot.
"""
from __future__ import annotations

from typing import Optional

# ---------------------------------------------------------------------------
# Mapping tables
# ---------------------------------------------------------------------------

# Stack depth → representative chip count (1 chip = 1 BB)
_STACK_CHIPS: dict[str, float] = {
    "short":     20.0,   # <20bb  — use 20bb as the ceiling representative
    "medium":    35.0,   # 20–40bb midpoint
    "deep":      70.0,   # 40–100bb midpoint
    "very_deep": 150.0,  # 100bb+ — use 150bb as a common deep-stack figure
}

# Standard 2-player raised pot used as the denominator for bet sizing.
# Represents: 3bb open + 3bb call = 6bb in the pot before the flop.
_BASE_POT: float = 6.0

# Facing action → fraction of pot.  None signals all-in (bet = effective stack).
_ACTION_FRACTIONS: dict[str, Optional[float]] = {
    "check":  0.0,
    "small":  0.30,
    "medium": 0.50,
    "large":  0.75,
    "pot":    1.0,
    "all_in": None,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def adapt_fast_inputs(stack_depth: str, facing_action: str) -> dict:
    """
    Convert fast-mode categorical inputs into numeric engine parameters.

    Returns a dict with keys: pot, bet, stack, players.
    All values are floats/ints ready to pass directly to the engine.
    """
    stack    = _STACK_CHIPS.get(stack_depth, _STACK_CHIPS["medium"])
    pot      = _BASE_POT
    fraction = _ACTION_FRACTIONS.get(facing_action)
    bet      = stack if fraction is None else round(pot * fraction, 1)

    return {
        "pot":     pot,
        "bet":     bet,
        "stack":   stack,
        "players": 2,
    }


def get_sizing_category(action: str, spr: float) -> Optional[str]:
    """
    Recommend a bet-sizing label when the engine says to raise.

    Returns one of: 'small' | 'medium' | 'large' | 'jam' | None.
    None means no sizing recommendation (action is call or fold).
    """
    if not action.upper().startswith("RAISE"):
        return None
    if spr < 2.0:
        return "jam"
    if spr < 4.0:
        return "large"
    if spr < 8.0:
        return "medium"
    return "small"
