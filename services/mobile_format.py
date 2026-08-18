"""
Mobile response formatting.

Maps the decision engine's internal action strings (FOLD / CHECK / CALL /
"RAISE <n>" / "BLUFF <n>") to the six-word vocabulary the mobile client
displays: FOLD, CHECK, CALL, BET, RAISE, ALL-IN.  Pure formatting — no
poker logic lives here.
"""
from __future__ import annotations


def mobile_action_label(action: str, bet: float, stack: float) -> str:
    """
    action: the engine's action string, e.g. "CALL" or "RAISE 45".
    bet:    the facing bet used by the engine for this decision (0 = none).
    stack:  hero's stack used by the engine for this decision.

    RAISE/BLUFF committing hero's full remaining stack becomes ALL-IN.
    Otherwise: no bet was facing hero -> BET (opening the wagering round);
    a bet was facing hero -> RAISE (increasing it).
    """
    parts = action.split()
    kind  = parts[0]

    if kind in ("FOLD", "CHECK", "CALL"):
        return kind

    # RAISE / BLUFF — parts[1] is the chip amount.
    try:
        amount = float(parts[1])
    except (IndexError, ValueError):
        amount = 0.0

    if stack > 0 and amount >= stack - 1e-6:
        return "ALL-IN"
    return "BET" if bet <= 0 else "RAISE"
