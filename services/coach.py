"""
Poker coach layer: decision tags, multi-bullet reasoning, UX signals, and
what-if scenario analysis.  All functions are stateless / pure.
"""
from __future__ import annotations

from services.ev import calculate_call_ev


# ===========================================================================
# DECISION TAGS
# ===========================================================================

def classify_decision_tags(
    action: str,
    hand_class: str,
    win_rate: float,
    spr: float,
    stage: str,
    range_advantage: float,
    is_bluff_catch: bool,
    texture: dict,
    fold_eq: float,
) -> list:
    """
    Returns a list of intent labels describing *why* this action is taken.

    Possible tags: VALUE | THIN_VALUE | BLUFF | PROTECTION | TRAP | BLUFF_CATCH | NEUTRAL | FOLD
    """
    tags: list = []
    action_type = action.split()[0] if action else "FOLD"

    if action_type == "FOLD":
        return ["FOLD"]

    # VALUE: raising / betting a clearly strong holding for value
    if action_type == "RAISE" and hand_class in ("nuts", "near_nuts", "strong_made"):
        tags.append("VALUE")

    # THIN_VALUE: betting a marginal hand that is slightly ahead of opponent's range
    if (
        action_type == "RAISE"
        and hand_class in ("medium_made", "weak_made")
        and win_rate >= 0.52
    ):
        tags.append("THIN_VALUE")

    # BLUFF: semi-bluff or pure bluff with little or no showdown value
    if action_type == "BLUFF":
        tags.append("BLUFF")

    # PROTECTION: raising a made hand on a wet board to deny draw equity
    if (
        action_type == "RAISE"
        and hand_class in ("strong_made", "medium_made")
        and texture.get("wetness", 0.0) >= 0.50
        and stage in ("flop", "turn")
    ):
        tags.append("PROTECTION")

    # TRAP: slow-playing a very strong hand to disguise strength and build the pot
    if (
        action_type == "CALL"
        and hand_class in ("nuts", "near_nuts")
        and spr >= 3.0
    ):
        tags.append("TRAP")

    # BLUFF_CATCH: calling with a marginal hand that beats opponent's bluff range
    if is_bluff_catch:
        tags.append("BLUFF_CATCH")

    return tags if tags else ["NEUTRAL"]


# ===========================================================================
# REASONING BULLETS
# ===========================================================================

_HAND_DESC: dict = {
    "nuts":        "monster hand (effective nuts)",
    "near_nuts":   "near-nut strength",
    "strong_made": "strong made hand",
    "medium_made": "medium-strength made hand",
    "weak_made":   "weak made hand (bottom pair / low kicker)",
    "strong_draw": "strong draw (flush draw or OESD)",
    "weak_draw":   "weak draw (gutshot / backdoor)",
    "air":         "complete air — no made hand or draw",
}

_PROFILE_NOTES: dict = {
    "fish":  "Opponent (fish): bluffing eliminated — fish don't fold; maximised value sizing.",
    "tight": "Opponent (tight): bluff frequency increased, value sized slightly smaller — tight players over-fold to aggression.",
    "loose": "Opponent (loose): bluffs reduced, value sized larger — loose players call too widely.",
    "reg":   "",
}


def build_reasoning(
    action: str,
    hand_class: str,
    win_rate: float,
    call_ev: float,
    raise_ev: float,
    stage: str,
    fold_eq: float,
    range_advantage: float,
    blockers: dict,
    spr: float,
    texture: dict,
    player_profile: str,
    tags: list,
    num_players: int,
    pot_odds: float,
) -> list:
    """
    Returns plain-English bullet points explaining the recommended action
    from a coaching / decision-analysis perspective.
    """
    bullets: list = []
    action_type = action.split()[0] if action else "FOLD"
    wetness = texture.get("wetness", 0.5)

    # 1. Hand strength
    bullets.append(
        f"Hand: {_HAND_DESC.get(hand_class, hand_class)} "
        f"— {win_rate:.0%} equity vs opponent's range"
    )

    # 2. Range advantage
    if range_advantage > 0.15:
        bullets.append(
            f"Range advantage: hero's range hits this board harder "
            f"(+{range_advantage:.0%}), supporting aggression"
        )
    elif range_advantage < -0.10:
        bullets.append(
            f"Range disadvantage: opponent's range connects better "
            f"({range_advantage:+.0%}) — proceed cautiously"
        )

    # 3. Board texture
    if texture.get("dry_board"):
        bullets.append(
            "Board texture: dry/rainbow — bluffs are highly credible; "
            "opponents with draws fold frequently"
        )
    elif wetness >= 0.65:
        bullets.append(
            "Board texture: very wet (flush + straight draws present) — "
            "made hands need protection; draws call profitably with their equity"
        )
    elif texture.get("paired"):
        bullets.append(
            "Board texture: paired board — full-house combos in play; "
            "polarisation increases on later streets"
        )
    elif wetness >= 0.35:
        bullets.append(
            "Board texture: moderately connected — balanced mix of draws and made hands"
        )

    # 4. SPR
    if spr <= 2.0:
        bullets.append(
            f"SPR {spr:.1f} (shallow): stack is nearly committed — "
            "getting it in is often mandatory with any reasonable equity"
        )
    elif spr <= 5.0:
        bullets.append(
            f"SPR {spr:.1f} (medium): strong hands should build the pot aggressively "
            "before stack leverage disappears"
        )
    elif spr >= 15.0:
        bullets.append(
            f"SPR {spr:.1f} (very deep): preserve stack; commit only with the "
            "top of your range"
        )
    else:
        bullets.append(f"SPR {spr:.1f}: comfortable aggression zone for strong made hands")

    # 5. Blockers
    blocker_score = blockers.get("blocker_score", 0.0)
    if blockers.get("blocks_nuts"):
        bullets.append(
            "Blockers: holding a nut blocker — removes opponent value combos, "
            "making bluffs/thin calls more profitable"
        )
    elif blocker_score >= 0.30:
        bullets.append(
            f"Blockers: useful blocker configuration (score {blocker_score:.2f}) "
            "supports the current line"
        )
    elif action_type == "BLUFF" and blocker_score < 0.20:
        bullets.append(
            f"Blocker warning: weak blockers ({blocker_score:.2f}) — "
            "consider reducing bluff frequency in this spot"
        )

    # 6. Fold equity and EV
    if action_type in ("RAISE", "BLUFF"):
        if fold_eq >= 0.35:
            bullets.append(
                f"Fold equity {fold_eq:.0%}: high — this bet is profitable "
                "even without showdown equity"
            )
        elif fold_eq >= 0.20:
            bullets.append(
                f"Fold equity {fold_eq:.0%}: moderate — combined with showdown "
                f"equity ({win_rate:.0%}) this line is +EV"
            )
        else:
            bullets.append(
                f"Fold equity {fold_eq:.0%}: low — value comes primarily from "
                "hand strength, not forcing folds"
            )
        ev_gap = raise_ev - call_ev
        bullets.append(
            f"EV edge: {action_type} ({raise_ev:+.1f}) beats calling "
            f"({call_ev:+.1f}) by {ev_gap:+.1f} chips"
        )
    elif action_type == "CALL":
        bullets.append(
            f"EV: call ({call_ev:+.1f} chips) — raising ({raise_ev:+.1f}) is not "
            "significantly better; pot control is preferred here"
        )
    elif action_type == "FOLD":
        bullets.append(
            f"EV: call ({call_ev:+.1f} chips) is negative — folding preserves chips"
        )

    # 7. Player profile note
    note = _PROFILE_NOTES.get(player_profile, "")
    if note:
        bullets.append(note)

    # 8. Multi-way note
    if num_players > 2:
        bullets.append(
            f"Multi-way ({num_players} players): hand-strength requirements rise sharply; "
            "bluffs are generally unprofitable in multi-way pots"
        )

    return bullets


# ===========================================================================
# UX SIGNALS
# ===========================================================================

def compute_ux_signals(
    action: str,
    win_rate: float,
    confidence: float,
    fold_eq: float,
    spr: float,
    hand_class: str,
    stage: str,
    player_profile: str,
) -> dict:
    """
    Returns confidence_score, aggression_score (0–1), and risk_level (low/medium/high)
    for front-end display / colour coding.
    """
    action_type = action.split()[0] if action else "FOLD"

    # Aggression score
    _AGG_BASE: dict = {
        "FOLD": 0.00, "CALL": 0.30, "RAISE": 0.78, "BLUFF": 0.92,
    }
    aggression_score = _AGG_BASE.get(action_type, 0.30)

    # Trap: calling with the nuts looks passive but is intentionally deceptive
    if action_type == "CALL" and hand_class in ("nuts", "near_nuts") and spr >= 3.0:
        aggression_score = 0.12

    # Risk level
    if action_type == "FOLD":
        risk_level = "low"
    elif action_type == "CALL":
        if confidence >= 0.65 or hand_class in ("nuts", "near_nuts", "strong_made"):
            risk_level = "low"
        elif spr <= 3.0 and hand_class not in (
            "nuts", "near_nuts", "strong_made", "strong_draw"
        ):
            risk_level = "high"
        else:
            risk_level = "medium"
    else:  # RAISE / BLUFF
        if hand_class in ("nuts", "near_nuts") and confidence >= 0.65:
            risk_level = "low"
        elif fold_eq >= 0.30 and confidence >= 0.55:
            risk_level = "medium"
        else:
            risk_level = "high"

    return {
        "confidence_score": round(confidence, 3),
        "aggression_score": round(aggression_score, 3),
        "risk_level":       risk_level,
    }


# ===========================================================================
# WHAT-IF ENGINE
# ===========================================================================

def compute_what_if(
    win_rate: float,
    pot: float,
    bet: float,
    stage: str,
    hand_class: str,
    texture: dict,
    blockers: dict,
    spr: float,
    call_ev: float,
) -> dict:
    """
    Estimates two counterfactual scenarios:
      1. If the opponent raises back (hero's optimal response + EV estimate)
      2. If the next community card is favorable or unfavorable (flop/turn only)
    """
    what_if: dict = {}

    # -------------------------------------------------------------------
    # Scenario 1: opponent raises back
    # -------------------------------------------------------------------
    if bet > 0:
        approx_3bet      = max(pot * 0.75, bet * 2.5)
        total_pot_after  = pot + approx_3bet + bet
        pot_odds_3bet    = approx_3bet / total_pot_after if total_pot_after > 0 else 0.40

        if hand_class in ("nuts", "near_nuts"):
            vs_action = "JAM / CALL — hand is too strong to fold vs any raise"
            vs_ev     = round(win_rate * (pot + approx_3bet) - (1 - win_rate) * approx_3bet, 2)
        elif hand_class == "strong_made" and win_rate >= pot_odds_3bet * 1.05:
            vs_action = "CALL — sufficient equity to continue vs re-raise"
            vs_ev     = round(win_rate * (pot + approx_3bet) - (1 - win_rate) * approx_3bet, 2)
        elif hand_class == "strong_draw" and stage != "river":
            vs_action = "CALL — strong draw has implied odds to continue"
            vs_ev     = round(
                win_rate * 1.15 * (pot + approx_3bet) - (1 - win_rate) * approx_3bet, 2
            )
        else:
            vs_action = (
                f"FOLD — {win_rate:.0%} equity is below break-even "
                f"({pot_odds_3bet:.0%}) vs re-raise"
            )
            vs_ev = round(-bet, 2)

        what_if["if_opponent_raises"] = {
            "recommended_action": vs_action,
            "estimated_ev":       vs_ev,
            "break_even_equity":  round(pot_odds_3bet, 3),
            "hero_equity":        round(win_rate, 3),
        }

    # -------------------------------------------------------------------
    # Scenario 2: next community card (flop/turn only)
    # -------------------------------------------------------------------
    if stage in ("flop", "turn"):
        if hand_class in ("strong_draw", "weak_draw"):
            # Favorable: draw completes
            improved_wr = min(0.88, win_rate + 0.28)
            improved_ev = round(calculate_call_ev(improved_wr, pot, bet), 2)
            what_if["if_favorable_card"] = {
                "scenario":       "draw completes",
                "new_equity_est": round(improved_wr, 3),
                "ev_delta":       round(improved_ev - call_ev, 2),
                "coaching":       "Shift to VALUE betting — hand is now strong made",
            }

            # Unfavorable: draw misses
            bricked_wr  = max(0.05, win_rate - 0.15)
            bricked_ev  = round(calculate_call_ev(bricked_wr, pot, bet), 2)
            board_danger = (
                "flush completes for opponent" if texture.get("flush_draw") else
                "straight completes for opponent" if texture.get("straight_draw") else
                "scare card / overcard appears"
            )
            what_if["if_unfavorable_card"] = {
                "scenario":       board_danger,
                "new_equity_est": round(bricked_wr, 3),
                "ev_delta":       round(bricked_ev - call_ev, 2),
                "coaching":       "Reassess: consider checking or folding to significant aggression",
            }

        elif hand_class in ("nuts", "near_nuts", "strong_made", "medium_made"):
            # Favorable: blank / safe card
            safe_wr = min(0.97, win_rate * 1.04)
            safe_ev = round(calculate_call_ev(safe_wr, pot, bet), 2)
            what_if["if_favorable_card"] = {
                "scenario":       "blank / safe card — hand stays ahead",
                "new_equity_est": round(safe_wr, 3),
                "ev_delta":       round(safe_ev - call_ev, 2),
                "coaching":       "Maintain aggression — continue building the pot",
            }

            # Unfavorable: scare card
            danger_wr = max(0.10, win_rate * 0.74)
            danger_ev = round(calculate_call_ev(danger_wr, pot, bet), 2)
            board_danger = (
                "flush completes" if texture.get("flush_draw") else
                "straight completes" if texture.get("straight_draw") else
                "overcard or board pairs"
            )
            what_if["if_unfavorable_card"] = {
                "scenario":       board_danger,
                "new_equity_est": round(danger_wr, 3),
                "ev_delta":       round(danger_ev - call_ev, 2),
                "coaching": (
                    "Strongly consider checking — hand may now be behind new nut combos"
                    if danger_wr < 0.40 else
                    "Reduce sizing or check back — some caution warranted"
                ),
            }

    return what_if
