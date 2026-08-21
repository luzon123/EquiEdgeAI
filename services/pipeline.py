"""
Shared structured-state -> decision pipeline.

Single source of truth for turning a numeric game state (hand, board, pot,
bet, stack, position, ...) into a full decision response.  Both POST
/decision and POST /mobile/analyze call this same function, so there is
exactly one code path that invokes the equity / EV / decision engine.  No
route may reimplement any part of this computation.
"""
from __future__ import annotations

import time
from typing import Optional

from config import DEFAULT_SIMULATIONS, MIN_SIMULATIONS, MAX_SIMULATIONS, QUICK_SIMULATIONS
from services.board_analysis import analyze_board_texture
from services.hand_classification import classify_hero_hand
from services.blockers import calculate_blocker_score
from services.ranges import estimate_range_advantage
from services.ev import calculate_spr, calculate_pot_odds, effective_call
from services.equity import simulate_equity
from services.exploit_engine import compute_population_adjustment_factor
from services.decision_engine import (
    decide_action,
    calculate_decision_confidence,
    generate_explanation,
)
from services.coach import (
    classify_decision_tags,
    build_reasoning,
    compute_ux_signals,
    compute_what_if,
)
from utils.cards import detect_stage
from utils.logging_setup import get_logger

logger = get_logger()


def resolve_simulations(mode: str, requested: Optional[int] = None) -> int:
    """Simulation-count policy shared by every caller that doesn't manage
    its own fixed budget (fast mode sets FAST_SIMULATIONS directly)."""
    if mode == "quick":
        return QUICK_SIMULATIONS
    base = DEFAULT_SIMULATIONS if requested is None else int(requested)
    return max(MIN_SIMULATIONS, min(MAX_SIMULATIONS, base))


def run_decision_pipeline(
    *,
    hand: list,
    board: list,
    players: int,
    pot: float,
    bet: float,
    stack: float,
    position: str,
    villain_stack: Optional[float] = None,
    player_profile: str = "reg",
    mode: str = "full",
    line: str = "none",
    has_initiative: bool = False,
    num_raises: int = 1,
    villain_position: Optional[str] = None,
    num_simulations: Optional[int] = None,
) -> dict:
    """
    Run the full equity -> EV -> decision pipeline.

    Returns the same response shape POST /decision has always returned
    (action, win_rate, pot_odds, ev_call, ev_raise, ev_breakdown, fold_equity,
    spr, hand_class, board_texture, blockers, range_advantage, confidence,
    explanation, decision_tags, reasoning, ux_signals, player_profile,
    population_adjustment, what_if), plus a leading-underscore raw stage/bet
    for callers that need to post-process (e.g. sizing_category, mobile
    action-label mapping) without recomputing engine state.

    Raises on internal engine failure — callers own their own error
    logging and response shape, since what's safe to tell a browser client
    differs from what's safe to tell a mobile client.
    """
    # Engine convention: `pot` is the TOTAL pot with any facing bet already
    # committed.  bet > pot is impossible under that convention, so such a
    # payload proves the caller passed the pot BEFORE the facing bet —
    # normalise it once here so every downstream formula sees one convention.
    if bet > pot:
        logger.info(
            "Pot convention normalised: pot %.1f excluded the facing bet %.1f "
            "— using total pot %.1f.", pot, bet, pot + bet,
        )
        pot += bet

    stage = detect_stage(board)

    # Effective stack: SPR should use min(hero, villain) so a short villain
    # doesn't give hero a false deep-stack SPR.  villain_stack defaults to
    # hero's stack when not supplied (assumes symmetric stacks = safe baseline).
    eff_villain_stack = stack if villain_stack is None else float(villain_stack)
    eff_stack = min(stack, eff_villain_stack) if eff_villain_stack > 0 else stack

    # 3-bet+ pot: only true when hero faces a SECOND (or later) preflop raise.
    is_3bet_pot = (stage == "preflop" and bet > 0 and num_raises >= 2)

    if num_simulations is None:
        num_simulations = resolve_simulations(mode)

    logger.info(
        "Request | mode=%s stage=%s pos=%s hand=%s board=%s "
        "players=%d pot=%.1f bet=%.1f stack=%.1f sims=%d line=%s profile=%s",
        mode, stage, position, hand, board, players, pot, bet, stack,
        num_simulations, line, player_profile,
    )

    texture         = analyze_board_texture(board)
    hand_class      = classify_hero_hand(hand, board)
    blockers        = calculate_blocker_score(hand, board, texture)
    range_advantage = estimate_range_advantage(position, board, stage, texture)
    spr             = calculate_spr(eff_stack, pot)   # effective stack, not hero stack
    # Pot odds on hero's REAL price: an oversized bet is only callable up
    # to hero's stack, and the uncallable excess isn't in hero's pot.
    odds_pot, odds_bet = effective_call(pot, bet, stack)
    pot_odds        = calculate_pot_odds(odds_pot, odds_bet)

    # perf_counter() timestamps only (not pre-computed durations): this
    # function is shared by /decision and /mobile/analyze, each of which
    # wants elapsed-since-ITS-OWN-request-start numbers, which only the
    # caller knows. routes/api.py strips this key before responding (see
    # its existing _stage/_bet/_stack pop) so it never reaches either
    # public API response — instrumentation-only, no contract change.
    t_equity_start = time.perf_counter()
    win_rate = simulate_equity(
        hand, board, players, position, num_simulations, stage, texture,
        is_3bet_pot=is_3bet_pot, villain_position=villain_position,
        first_in=(bet == 0),
    )
    t_equity_end = time.perf_counter()

    # On a complete board, the nuts hand wins every possible runout by
    # definition — no simulation approximation needed.
    if hand_class == "nuts" and len(board) == 5:
        win_rate = 1.0

    t_decision_start = time.perf_counter()
    action, ev_call, ev_raise, fold_eq, ev_breakdown = decide_action(
        win_rate=win_rate,
        pot=pot,
        bet=bet,
        stack=stack,
        stage=stage,
        position=position,
        num_players=players,
        hand_class=hand_class,
        texture=texture,
        blockers=blockers,
        range_advantage=range_advantage,
        spr=spr,
        line=line,
        player_profile=player_profile,
        has_initiative=has_initiative,
    )
    t_decision_end = time.perf_counter()

    is_bluff_catch = ev_breakdown.pop("bluff_catch", False)
    catch_reason   = ev_breakdown.pop("catch_reason", "")

    confidence = calculate_decision_confidence(
        win_rate, ev_call, ev_raise, action, num_simulations, hand_class
    )

    explanation = generate_explanation(
        action, hand_class, win_rate, ev_call, ev_raise,
        stage, fold_eq, range_advantage, blockers, spr,
        is_bluff_catch, catch_reason,
    )

    tags = classify_decision_tags(
        action=action,
        hand_class=hand_class,
        win_rate=win_rate,
        spr=spr,
        stage=stage,
        range_advantage=range_advantage,
        is_bluff_catch=is_bluff_catch,
        texture=texture,
        fold_eq=fold_eq,
    )

    reasoning = build_reasoning(
        action=action,
        hand_class=hand_class,
        win_rate=win_rate,
        call_ev=ev_call,
        raise_ev=ev_raise,
        stage=stage,
        fold_eq=fold_eq,
        range_advantage=range_advantage,
        blockers=blockers,
        spr=spr,
        texture=texture,
        player_profile=player_profile,
        tags=tags,
        num_players=players,
        pot_odds=pot_odds,
    )

    ux_signals = compute_ux_signals(
        action=action,
        win_rate=win_rate,
        confidence=confidence,
        fold_eq=fold_eq,
        spr=spr,
        hand_class=hand_class,
        stage=stage,
        player_profile=player_profile,
    )

    population_adj = compute_population_adjustment_factor(player_profile, stage)

    what_if = {}
    if mode == "full":
        what_if = compute_what_if(
            win_rate=win_rate,
            pot=pot,
            bet=bet,
            stage=stage,
            hand_class=hand_class,
            texture=texture,
            blockers=blockers,
            spr=spr,
            call_ev=ev_call,
        )

    logger.info(
        "Response | mode=%s wr=%.4f ev_c=%.2f ev_r=%.2f "
        "class=%s spr=%.2f conf=%.2f action=%s tags=%s",
        mode, win_rate, ev_call, ev_raise,
        hand_class, spr, confidence, action, tags,
    )

    return {
        "action":          action,
        "win_rate":        round(win_rate,       4),
        "pot_odds":        round(pot_odds,        4),
        "ev_call":         round(ev_call,         2),
        "ev_raise":        round(ev_raise,        2),
        "ev_breakdown":    ev_breakdown,
        "fold_equity":     round(fold_eq,         4),
        "spr":             round(spr,             2),
        "hand_class":      hand_class,
        "board_texture":   texture,
        "blockers":        blockers,
        "range_advantage": round(range_advantage, 4),
        "confidence":      confidence,
        "explanation":     explanation,
        "decision_tags":         tags,
        "reasoning":             reasoning,
        "ux_signals":            ux_signals,
        "player_profile":        player_profile,
        "population_adjustment": population_adj,
        "what_if":               what_if,
        # Raw values for callers that post-process without recomputing state.
        "_stage": stage,
        "_bet":   bet,
        "_stack": stack,
        # Instrumentation-only (latency audit) — raw perf_counter() timestamps,
        # not durations; see the comment above t_equity_start.
        "_perf": {
            "equity_start":   t_equity_start,
            "equity_end":     t_equity_end,
            "decision_start": t_decision_start,
            "decision_end":   t_decision_end,
        },
    }
