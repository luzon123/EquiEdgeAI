"""
Master decision logic, adaptive thresholds, confidence scoring, and
action explanation generation.
"""
from __future__ import annotations

from config import POSITION_AGGRESSION, DEFAULT_SIMULATIONS
from services.ev import (
    calculate_pot_odds,
    calculate_call_ev,
    calculate_raise_ev,
    compute_raise_size,
    evaluate_bluff_catch,
    get_equity_realization,
    should_bluff,
    spr_aggression_factor,
    spr_commitment_threshold,
)
from services.exploit_engine import get_profile
from utils.logging_setup import get_logger

logger = get_logger()


# ===========================================================================
# ADAPTIVE THRESHOLDS
# ===========================================================================

def adaptive_thresholds(
    num_players: int,
    spr: float,
    texture: dict,
    hand_class: str,
    position: str,
    range_advantage: float,
    in_position: bool = True,
) -> dict:
    opponents = num_players - 1
    wetness   = texture.get("wetness", 0.5)

    call_base  = 0.28 + opponents * 0.035
    raise_base = 0.52 + opponents * 0.045

    agg = spr_aggression_factor(spr)
    call_base  /= agg
    raise_base /= agg

    call_base  += wetness * 0.05
    raise_base += wetness * 0.06

    call_base  -= range_advantage * 0.05
    raise_base -= range_advantage * 0.07

    pos_disc    = POSITION_AGGRESSION.get(position, 0.5) * 0.05
    call_base  -= pos_disc
    raise_base -= pos_disc

    # IP: can be more aggressive (lower thresholds); OOP: tighten up
    ip_adj      = -0.03 if in_position else +0.03
    call_base  += ip_adj
    raise_base += ip_adj * 1.3   # raise threshold adjusts slightly more

    commit_floor = spr_commitment_threshold(spr, hand_class)
    return {
        "call_threshold":  max(0.20, min(call_base,  0.52)),
        "raise_threshold": max(commit_floor, min(raise_base, 0.82)),
    }


# ===========================================================================
# DECISION CONFIDENCE
# ===========================================================================

def calculate_decision_confidence(
    win_rate: float,
    call_ev: float,
    raise_ev: float,
    action: str,
    num_simulations: int,
    hand_class: str,
) -> float:
    """
    Returns 0–1 confidence score.
    High = clear decision; Low = marginal/close spot.
    """
    # EV gap between the top two options
    fold_ev  = 0.0  # fold is always ~0 EV by definition
    all_evs  = sorted([call_ev, raise_ev, fold_ev], reverse=True)
    ev_gap   = abs(all_evs[0] - all_evs[1])
    baseline = max(abs(call_ev), abs(raise_ev), 1.0)
    ev_conf  = min(1.0, ev_gap / (baseline * 0.5 + 1.0))

    # Simulation depth
    sim_conf = min(1.0, num_simulations / DEFAULT_SIMULATIONS)

    # Hand clarity (clear nuts or clear air = higher confidence than marginal hands)
    clarity_map = {
        "nuts": 0.92, "near_nuts": 0.85, "strong_made": 0.72,
        "medium_made": 0.55, "weak_made": 0.50,
        "strong_draw": 0.62, "combo_draw": 0.70, "weak_draw": 0.45, "air": 0.68,
    }
    hand_conf = clarity_map.get(hand_class, 0.55)

    # Win-rate distance from break-even (further = clearer)
    wr_conf = min(1.0, abs(win_rate - 0.50) * 2.5)

    confidence = (
        0.35 * ev_conf
        + 0.20 * sim_conf
        + 0.25 * hand_conf
        + 0.20 * wr_conf
    )
    return max(0.05, min(1.0, round(confidence, 3)))


# ===========================================================================
# ACTION EXPLANATION
# ===========================================================================

def generate_explanation(
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
    is_bluff_catch: bool,
    catch_reason: str,
) -> str:
    action_type = action.split()[0]

    if action_type == "FOLD":
        if stage == "river" and win_rate < 0.35:
            return "Fold: river — population under-bluffs, negative EV call"
        if win_rate < 0.28:
            return f"Fold: insufficient equity ({win_rate:.0%}), clear underdog"
        return "Fold: pot odds not satisfied and call EV negative"

    if action_type == "CALL":
        if is_bluff_catch:
            return catch_reason or "Call as bluff-catcher: range is capped, blockers support"
        if hand_class in ("strong_draw", "weak_draw"):
            return f"Call: draw with implied odds ({win_rate:.0%} equity, +EV to continue)"
        if raise_ev <= call_ev * 1.03:
            return f"Call: raise not clearly better (ev_call={call_ev:+.1f} vs ev_raise={raise_ev:+.1f})"
        return f"Call: positive EV ({call_ev:+.1f}), preserving pot control"

    if action_type == "RAISE":
        if hand_class in ("nuts", "near_nuts"):
            return f"Value raise: {hand_class} — building pot, fold_eq={fold_eq:.0%}"
        if range_advantage > 0.25:
            return f"Raise: range advantage on board + fold_eq={fold_eq:.0%}"
        ev_delta = raise_ev - call_ev
        return f"Raise: EV gain {ev_delta:+.1f} over call (fold_eq={fold_eq:.0%})"

    if action_type == "BLUFF":
        if blockers.get("blocks_nuts"):
            return f"Bluff: nut blocker removes opponent value combos, fold_eq={fold_eq:.0%}"
        if stage == "flop":
            return f"Bluff: population over-folds to flop aggression, fold_eq={fold_eq:.0%}"
        return "Bluff: position + range advantage + dry board justify aggression"

    return f"{action_type}: highest EV action given current game state"


# ===========================================================================
# MASTER DECISION LOGIC
# ===========================================================================

def decide_action(
    win_rate: float,
    pot: float,
    bet: float,
    stack: float,
    stage: str,
    position: str,
    num_players: int,
    hand_class: str,
    texture: dict,
    blockers: dict,
    range_advantage: float,
    spr: float,
    line: str = "none",
    player_profile: str = "reg",
    has_initiative: bool = False,
) -> tuple[str, float, float, float, dict]:
    """
    Returns (action_str, call_ev, raise_ev, fold_eq, raise_ev_breakdown).
    """
    # --- Player-profile exploit multipliers -----------------------------------
    _profile         = get_profile(player_profile)
    fold_eq_mult     = _profile["fold_equity_mult"]
    bluff_freq_mult  = _profile["bluff_freq_mult"]
    value_size_mult  = _profile["value_size_mult"]
    # --------------------------------------------------------------------------

    in_position = position in {"BTN", "CO"}

    pot_odds = calculate_pot_odds(pot, bet)
    wetness  = texture.get("wetness", 0.5)
    pos_factor = POSITION_AGGRESSION.get(position, 0.5)

    # Board rank information: use high/low card board flags from texture to
    # refine range advantage — tight UTG/MP ranges dominate high boards,
    # wide BTN/CO ranges have an edge on low boards.
    hcb = texture.get("high_card_board", False)
    lcb = texture.get("low_card_board", False)
    if hcb and position in ("UTG", "MP"):
        range_advantage = min(1.0, range_advantage + 0.08)
    elif lcb and position in ("BTN", "CO"):
        range_advantage = min(1.0, range_advantage + 0.06)

    # When hero is first to act (no bet facing), raw check EV (win_rate × pot)
    # overstates realized value — future streets with aggression and position
    # disadvantage will erode equity.  Apply realization factor for a fair baseline.
    if bet == 0:
        real    = get_equity_realization(stage, in_position, hand_class)
        call_ev = win_rate * pot * real
    else:
        call_ev = calculate_call_ev(win_rate, pot, bet)

    thresholds = adaptive_thresholds(
        num_players, spr, texture, hand_class, position, range_advantage,
        in_position=in_position,
    )

    # Baseline raise sizing + 3-outcome EV (with profile fold-equity scaling)
    raise_size          = compute_raise_size(pot, stack, stage, hand_class, texture,
                                              is_bluff=False, spr=spr)
    raise_size          = max(1, min(round(raise_size * value_size_mult), round(stack)))
    raise_ev, breakdown = calculate_raise_ev(
        win_rate, pot, bet, raise_size, stage, num_players, position, hand_class, texture,
        fold_eq_mult=fold_eq_mult, in_position=in_position, has_initiative=has_initiative,
    )
    fold_eq = breakdown["p_fold"]

    # Overbet candidate (polarised river spots)
    overbet_size = 0
    overbet_ev   = raise_ev
    use_overbet  = False
    if (stage == "river"
            and hand_class in ("nuts", "near_nuts", "air", "strong_draw", "combo_draw")
            and (spr >= 2.0)
            and (range_advantage > 0.15 or blockers.get("blocker_score", 0) >= 0.40)):
        overbet_size          = compute_raise_size(pot, stack, stage, hand_class, texture,
                                                    is_bluff=(hand_class in ("air", "strong_draw", "combo_draw")),
                                                    spr=spr, use_overbet=True)
        overbet_ev, ob_brkdwn = calculate_raise_ev(
            win_rate, pot, bet, overbet_size, stage, num_players, position, hand_class, texture,
            fold_eq_mult=fold_eq_mult, in_position=in_position, has_initiative=has_initiative,
        )
        if overbet_ev > raise_ev * 1.04:
            use_overbet = True
            raise_size  = overbet_size
            raise_ev    = overbet_ev
            breakdown   = ob_brkdwn
            fold_eq     = ob_brkdwn["p_fold"]

    logger.debug(
        "Decision | wr=%.3f pot_odds=%.3f ev_c=%.2f ev_r=%.2f fold_eq=%.3f "
        "class=%s spr=%.1f radv=%.2f stage=%s n=%d line=%s overbet=%s",
        win_rate, pot_odds, call_ev, raise_ev, fold_eq,
        hand_class, spr, range_advantage, stage, num_players, line, use_overbet,
    )

    # -----------------------------------------------------------------------
    # Helper closures
    # -----------------------------------------------------------------------
    def _raise_action(hc: str, ob: bool = False) -> tuple[str, float, float, float, dict]:
        rs          = compute_raise_size(pot, stack, stage, hc, texture,
                                         is_bluff=False, spr=spr, use_overbet=ob)
        rs          = max(1, min(round(rs * value_size_mult), round(stack)))
        rev, brkdwn = calculate_raise_ev(win_rate, pot, bet, rs, stage,
                                          num_players, position, hc, texture,
                                          fold_eq_mult=fold_eq_mult,
                                          in_position=in_position,
                                          has_initiative=has_initiative)
        return f"RAISE {rs}", call_ev, rev, brkdwn["p_fold"], brkdwn

    def _bluff_action(hc: str, ob: bool = False) -> tuple[str, float, float, float, dict]:
        bs          = compute_raise_size(pot, stack, stage, hc, texture,
                                         is_bluff=True, spr=spr, use_overbet=ob)
        bev, brkdwn = calculate_raise_ev(win_rate, pot, bet, bs, stage,
                                          num_players, position, hc, texture,
                                          fold_eq_mult=fold_eq_mult,
                                          in_position=in_position,
                                          has_initiative=has_initiative)
        return f"BLUFF {bs}", call_ev, bev, brkdwn["p_fold"], brkdwn

    def _call() -> tuple[str, float, float, float, dict]:
        return "CALL", call_ev, raise_ev, fold_eq, breakdown

    def _fold() -> tuple[str, float, float, float, dict]:
        # When there is no bet facing hero, folding means checking behind —
        # return CHECK so the API label matches the actual action.
        action = "FOLD" if bet > 0 else "CHECK"
        return action, call_ev, raise_ev, fold_eq, breakdown

    # -----------------------------------------------------------------------
    # BLUFF-CATCH CHECK (evaluated before all other logic)
    # Only relevant when we are facing a bet (bet > 0).
    # Skip for nuts/near_nuts — those hands always want to raise into a bet,
    # never flat-call as a bluff-catcher.
    # -----------------------------------------------------------------------
    if bet > 0 and stage in ("flop", "turn", "river") and hand_class not in ("nuts", "near_nuts"):
        should_catch, catch_ev, catch_reason = evaluate_bluff_catch(
            win_rate, pot, bet, stage, num_players, hand_class,
            blockers, texture, range_advantage, line,
        )
        if should_catch:
            return "CALL", call_ev, raise_ev, fold_eq, {**breakdown, "bluff_catch": True, "catch_reason": catch_reason}

    # -----------------------------------------------------------------------
    # NUTS / NEAR-NUTS
    # -----------------------------------------------------------------------
    if hand_class in ("nuts", "near_nuts"):
        # Slow-play trap: wet multi-way board gives draws plenty of combos to
        # stack off against us — let them catch up and build the pot naturally.
        slow_play = (
            num_players >= 3
            and wetness >= 0.60
            and spr >= 4.0
            and stage in ("flop", "turn")
        )
        if not slow_play and (spr <= 3.0 or raise_ev > call_ev):
            return _raise_action(hand_class, use_overbet)
        return _call()

    # -----------------------------------------------------------------------
    # STRONG / MEDIUM / WEAK MADE HANDS
    # -----------------------------------------------------------------------
    if hand_class in ("strong_made", "medium_made", "weak_made", "strong", "medium"):
        # Thin value bet: medium-made hands on river that are slightly ahead.
        # EV calculation filters out marginal spots — no board-type gate needed.
        thin_value = (
            stage == "river"
            and hand_class in ("medium_made", "strong_made")
            and 0.52 <= win_rate <= 0.68
            and num_players == 2
        )
        if thin_value:
            tv_size          = compute_raise_size(pot, stack, stage, hand_class, texture,
                                                   is_bluff=False, spr=spr, thin_value=True)
            tv_ev, tv_brkdwn = calculate_raise_ev(win_rate, pot, bet, tv_size, stage,
                                                    num_players, position, hand_class, texture,
                                                    fold_eq_mult=fold_eq_mult,
                                                    in_position=in_position,
                                                    has_initiative=has_initiative)
            if tv_ev > call_ev * 1.04:
                return f"RAISE {tv_size}", call_ev, tv_ev, tv_brkdwn["p_fold"], tv_brkdwn

        if win_rate >= thresholds["raise_threshold"]:
            rs          = compute_raise_size(pot, stack, stage, hand_class, texture,
                                              is_bluff=False, spr=spr)
            rev, brkdwn = calculate_raise_ev(win_rate, pot, bet, rs, stage,
                                              num_players, position, hand_class, texture,
                                              fold_eq_mult=fold_eq_mult,
                                              in_position=in_position,
                                              has_initiative=has_initiative)
            # Require raise to be positive EV (better than folding at ≈0)
            # AND meaningfully better than calling.  Without the rev > 0 guard,
            # this fires when both call_ev and rev are negative (e.g. cal=-5,
            # rev=-3 → -3 ≥ -5.1 → raises into a losing spot).
            if rev > 0 and rev >= call_ev * 1.02:
                return f"RAISE {rs}", call_ev, rev, brkdwn["p_fold"], brkdwn
            return _call()

        if win_rate >= pot_odds and call_ev > 0:
            return _call()

        # Protection on wet boards: don't let draws realise equity cheaply.
        # On very wet boards heads-up with strong made, raise to deny equity;
        # on moderately wet or multi-way boards, call to control pot size.
        if (hand_class in ("strong_made", "medium_made", "strong")
                and win_rate >= pot_odds * 0.88
                and wetness >= 0.4
                and stage in ("flop", "turn")):
            if wetness >= 0.65 and hand_class == "strong_made" and num_players <= 2:
                return _raise_action(hand_class)
            return _call()

        # Range-balance raise: range advantage + blockers + dry board.
        # Use normal raise sizing (not bluff sizing) — an undersized raise
        # with a made hand is readable and leaks value.
        if (range_advantage > 0.20
                and blockers.get("blocker_score", 0) >= 0.30
                and texture.get("wetness", 1.0) <= 0.55
                and should_bluff(stage, num_players, position, texture,
                                  hand_class, blockers, range_advantage, spr, line,
                                  bluff_freq_mult=bluff_freq_mult,
                                  in_position=in_position,
                                  has_initiative=has_initiative)):
            return _raise_action(hand_class)

        return _fold()

    # -----------------------------------------------------------------------
    # COMBO DRAW (flush draw + open-ended straight draw, ~15 outs, ~54% HU)
    # Closer in equity to a made hand than a bare draw — play aggressively.
    # -----------------------------------------------------------------------
    if hand_class == "combo_draw":
        # Even facing a bet, raise as a semi-bluff — 15 outs gives enough
        # equity to semi-bluff profitably in most scenarios.
        if stage == "preflop" and bet == 0:
            if should_bluff(stage, num_players, position, texture,
                            hand_class, blockers, range_advantage, spr, line,
                            in_position=in_position, has_initiative=has_initiative):
                return _raise_action(hand_class)
            return _fold()

        if stage != "river":
            # Combo draws raise when there's any possibility of fold equity
            # OR the implied odds justify continuing.
            sr           = compute_raise_size(pot, stack, stage, hand_class, texture,
                                              is_bluff=True, spr=spr)
            sev, sbrkdwn = calculate_raise_ev(win_rate, pot, bet, sr, stage,
                                                  num_players, position, hand_class, texture,
                                                  fold_eq_mult=fold_eq_mult,
                                                  in_position=in_position,
                                                  has_initiative=has_initiative)
            if (sev > call_ev * 1.01
                    and num_players <= 3
                    and should_bluff(stage, num_players, position, texture,
                                     hand_class, blockers, range_advantage, spr, line,
                                     in_position=in_position, has_initiative=has_initiative)):
                return f"RAISE {sr}", call_ev, sev, sbrkdwn["p_fold"], sbrkdwn
            # Even without fold equity, the raw equity justifies calling
            if win_rate >= pot_odds * 0.80 or call_ev > -bet * 0.10:
                return _call()

        # River: missed combo draw — strong bluff candidate with remaining equity
        if should_bluff(stage, num_players, position, texture,
                         hand_class, blockers, range_advantage, spr, line,
                         in_position=in_position, has_initiative=has_initiative):
            ob_bluff = (blockers.get("blocker_score", 0) >= 0.40 and use_overbet)
            return _bluff_action("combo_draw", ob=ob_bluff)
        return _fold()

    # -----------------------------------------------------------------------
    # STRONG DRAW
    # -----------------------------------------------------------------------
    if hand_class in ("strong_draw", "draw"):
        # Implied-odds multiplier decays by street: more future streets to
        # realise implied value on the flop than on the turn.
        implied_mult = 1.30 if stage == "flop" else 1.10 if stage == "turn" else 1.18
        implied_wr   = win_rate * implied_mult

        if stage != "river":
            # Preflop first-in: suited connectors should open-raise or fold,
            # not limp (bet=0 → pot_odds=0 → implied_wr>=0 always True → limp).
            if stage == "preflop" and bet == 0:
                if should_bluff(stage, num_players, position, texture,
                                hand_class, blockers, range_advantage, spr, line,
                                in_position=in_position, has_initiative=has_initiative):
                    return _raise_action(hand_class)
                return _fold()

            if implied_wr >= pot_odds:
                sr           = compute_raise_size(pot, stack, stage, hand_class, texture,
                                                   is_bluff=True, spr=spr)
                sev, sbrkdwn = calculate_raise_ev(win_rate, pot, bet, sr, stage,
                                                   num_players, position, hand_class, texture,
                                                   fold_eq_mult=fold_eq_mult,
                                                   in_position=in_position,
                                                   has_initiative=has_initiative)
                if (sev > call_ev * 1.02
                        and pos_factor >= 0.35
                        and num_players <= 3
                        and should_bluff(stage, num_players, position, texture,
                                          hand_class, blockers, range_advantage, spr, line,
                                          in_position=in_position,
                                          has_initiative=has_initiative)):
                    return f"RAISE {sr}", call_ev, sev, sbrkdwn["p_fold"], sbrkdwn
                if call_ev > 0 or implied_wr >= pot_odds:
                    return _call()

        # River missed draw OR pot odds not covered
        if should_bluff(stage, num_players, position, texture,
                         hand_class, blockers, range_advantage, spr, line,
                         in_position=in_position, has_initiative=has_initiative):
            # Overbet bluff on river with strong blocker + missed draw
            ob_bluff = (stage == "river"
                        and blockers.get("blocker_score", 0) >= 0.45
                        and use_overbet)
            return _bluff_action("weak_draw", ob=ob_bluff)
        return _fold()

    # -----------------------------------------------------------------------
    # WEAK DRAW
    # -----------------------------------------------------------------------
    if hand_class == "weak_draw":
        implied_wr = win_rate * (1.12 if stage == "flop" else 1.05 if stage == "turn" else 1.08)
        if stage != "river" and implied_wr >= pot_odds and call_ev > -bet * 0.15:
            return _call()
        if should_bluff(stage, num_players, position, texture,
                         hand_class, blockers, range_advantage, spr, line,
                         in_position=in_position, has_initiative=has_initiative):
            return _bluff_action("weak_draw")
        return _fold()

    # -----------------------------------------------------------------------
    # AIR / UNCLASSIFIED
    # -----------------------------------------------------------------------
    # River air: population under-bluffs → our bluffs are less credible too,
    # BUT if we have strong blockers we exploit their tendency to over-fold large bets.
    if stage == "river":
        if blockers.get("blocker_score", 0) >= 0.55 and use_overbet:
            if should_bluff(stage, num_players, position, texture,
                             hand_class, blockers, range_advantage, spr, line,
                             in_position=in_position, has_initiative=has_initiative):
                return _bluff_action("air", ob=True)
        return _fold()

    # First-to-act (no bet): pot_odds=0, call_ev>0 always — must check bluff
    # explicitly or we'd always return CHECK and never bet air in position.
    if bet == 0:
        if should_bluff(stage, num_players, position, texture,
                         hand_class, blockers, range_advantage, spr, line,
                         in_position=in_position, has_initiative=has_initiative):
            return _bluff_action("air")
        return _fold()   # CHECK

    # Facing a bet: fold if equity < pot odds, unless a bluff is warranted
    if win_rate < pot_odds:
        if should_bluff(stage, num_players, position, texture,
                         hand_class, blockers, range_advantage, spr, line,
                         in_position=in_position, has_initiative=has_initiative):
            return _bluff_action("air")
        return _fold()

    if call_ev <= 0:
        if should_bluff(stage, num_players, position, texture,
                         hand_class, blockers, range_advantage, spr, line,
                         in_position=in_position, has_initiative=has_initiative):
            return _bluff_action("air")
        return _fold()

    if win_rate >= pot_odds * 1.10 and call_ev > 0:
        return _call()

    return _fold()
