"""
Bridge from the vision pipeline's typed game state to the decision
pipeline's numeric inputs.

This is the ONLY place that maps VisionGameState fields to
run_decision_pipeline() kwargs.  It performs translation and completeness /
confidence validation only — it never computes equity, EV, or a decision
itself, and it never invents a value the vision layer didn't actually
extract.
"""
from __future__ import annotations

from typing import Optional

from config import VALID_POSITIONS

# Below this extraction confidence, refuse to analyze rather than hand the
# engine a game state the vision model itself was not confident about.
# Calibrated loosely (not empirically) — see prompts.py rule 8 for how the
# model is instructed to set these; revisit once real screenshots are in.
MIN_OVERALL_CONFIDENCE:     float = 0.35
MIN_HERO_CARDS_CONFIDENCE:  float = 0.40


def build_decision_params(game_state: dict) -> tuple[Optional[dict], Optional[str]]:
    """
    Convert a validated vision game-state dict (VisionGameState.to_dict())
    into run_decision_pipeline() kwargs.

    Returns (params, None) on success, or (None, user_facing_error) when the
    extracted state is incomplete or too uncertain to safely decide from.

    Single-screenshot constraint: action history, precise villain identity,
    and 3-bet/4-bet context are not derivable from one screenshot, so line,
    has_initiative, num_raises, and villain_position all take the same safe
    defaults the manual /decision form uses when the user leaves them blank.
    """
    hero_cards = game_state.get("hero_cards") or []
    if len(hero_cards) != 2:
        return None, "Couldn't read your hole cards. Try a clearer screenshot."

    if game_state.get("hero_cards_confidence", 0.0) < MIN_HERO_CARDS_CONFIDENCE:
        return None, "Not confident enough in your hole cards. Try a clearer screenshot."

    overall_conf = game_state.get("overall_confidence", 0.0)
    if overall_conf < MIN_OVERALL_CONFIDENCE:
        return None, "Table isn't clear enough to analyze. Try a clearer screenshot."

    board = game_state.get("board")
    if board is None or len(board) not in (0, 3, 4, 5):
        return None, "Couldn't read the board cards. Try a clearer screenshot."

    pot = game_state.get("pot")
    if pot is None or pot < 0:
        return None, "Couldn't read the pot size. Try a clearer screenshot."

    hero_stack = game_state.get("hero_stack")
    if hero_stack is None or hero_stack <= 0:
        return None, "Couldn't read your stack. Try a clearer screenshot."

    position = game_state.get("hero_position")
    if not position or position not in VALID_POSITIONS:
        return None, "Couldn't determine your table position. Try a clearer screenshot."

    player_count = game_state.get("player_count")
    if not isinstance(player_count, int) or player_count < 2:
        return None, "Couldn't determine how many players are in the hand."

    # Facing bet: call_amount is what the ClubGG prompt's action_required
    # translates to (0 for check, the call size for call).  current_bet is
    # a fallback for other extraction shapes that populate it directly.
    bet = game_state.get("call_amount")
    if bet is None:
        bet = game_state.get("current_bet")
    if bet is None:
        bet = 0.0

    params = {
        "hand":     hero_cards,
        "board":    board,
        "players":  player_count,
        "pot":      float(pot),
        "bet":      float(bet),
        "stack":    float(hero_stack),
        "position": position,
        "villain_stack":    None,
        "player_profile":   "reg",
        "line":             "none",
        "has_initiative":   False,
        "num_raises":       1,
        "villain_position": None,
    }
    return params, None
