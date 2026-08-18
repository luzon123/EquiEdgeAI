"""
API routes: POST /decision and GET /health.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from extensions import csrf, limiter

from config import (
    DEFAULT_SIMULATIONS, MIN_SIMULATIONS, MAX_SIMULATIONS, QUICK_SIMULATIONS,
    FAST_SIMULATIONS,
)
from utils.cards import normalize_card, detect_stage
from utils.validation import validate_request, validate_fast_request
from utils.logging_setup import get_logger
from services.board_analysis import analyze_board_texture
from services.hand_classification import classify_hero_hand
from services.blockers import calculate_blocker_score
from services.ranges import estimate_range_advantage
from services.ev import calculate_spr, calculate_pot_odds, effective_call
from services.equity import simulate_equity
from services.exploit_engine import compute_population_adjustment_factor, get_profile
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
from services.fast_mode_adapter import adapt_fast_inputs, get_sizing_category

api_bp = Blueprint("api", __name__)
logger = get_logger()



# ---------------------------------------------------------------------------
# POST /decision
# ---------------------------------------------------------------------------
@api_bp.route("/decision", methods=["POST"])
@csrf.exempt
@limiter.limit("120 per minute")
def decision_endpoint():
    # ── Parse request ─────────────────────────────────────────────────────
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({
            "error": "Request body must be valid JSON with Content-Type: application/json."
        }), 400

    # ── Detect mode ───────────────────────────────────────────────────────
    request_mode = data.get("mode", "full")
    is_fast      = request_mode == "fast"

    # ── Branch: parameter extraction ──────────────────────────────────────
    if is_fast:
        error_msg = validate_fast_request(data)
        if error_msg:
            return jsonify({"error": error_msg}), 400

        adapted        = adapt_fast_inputs(data["stack_depth"], data["facing_action"])
        hand           = [normalize_card(c) for c in data["hand"]]
        board          = [normalize_card(c) for c in data["board"]]
        position       = data["position"].upper()
        players        = adapted["players"]
        pot            = adapted["pot"]
        bet            = adapted["bet"]
        stack          = adapted["stack"]
        player_profile = "reg"
        mode           = "fast"
        line             = "none"
        has_initiative   = False
        num_raises       = 1
        villain_position = None
        num_simulations  = FAST_SIMULATIONS

    else:
        error_msg = validate_request(data)
        if error_msg:
            logger.warning("Bad request: %s | payload=%s", error_msg, data)
            return jsonify({"error": error_msg}), 400

        hand             = [normalize_card(c) for c in data["hand"]]
        board            = [normalize_card(c) for c in data["board"]]
        players          = int(data["players"])
        pot              = float(data["pot"])
        bet              = float(data["bet"])
        stack            = float(data["stack"])
        position         = data["position"].upper()
        player_profile   = data.get("player_profile", "reg")
        mode             = data.get("mode", "full")
        line             = data.get("line", "none")
        has_initiative   = bool(data.get("has_initiative", False))
        num_raises       = int(data.get("num_raises", 1))
        villain_position = data.get("villain_position")
        if villain_position:
            villain_position = villain_position.upper()

        if mode == "quick":
            num_simulations = QUICK_SIMULATIONS
        else:
            raw_sims        = data.get("simulations", DEFAULT_SIMULATIONS)
            num_simulations = max(MIN_SIMULATIONS, min(MAX_SIMULATIONS, int(raw_sims)))

    # ── Shared computation ────────────────────────────────────────────────
    # Engine convention: `pot` is the TOTAL pot with any facing bet already
    # committed.  bet > pot is impossible under that convention, so such a
    # payload proves the client passed the pot BEFORE villain's bet —
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
    # hero's stack if not provided (assumes symmetric stacks = safe baseline).
    if not is_fast:
        villain_stack = float(data.get("villain_stack", stack))
    else:
        villain_stack = stack
    eff_stack = min(stack, villain_stack) if villain_stack > 0 else stack

    # 3-bet+ pot: only true when hero faces a SECOND (or later) preflop raise.
    # A single open-raise is not a 3-bet — conflating the two previously forced
    # villain's range down to premiums+strong hands even vs a plain open,
    # understating villain's realistic range and skewing equity too high.
    is_3bet_pot = (stage == "preflop" and bet > 0 and num_raises >= 2)
    logger.info(
        "Request | mode=%s stage=%s pos=%s hand=%s board=%s "
        "players=%d pot=%.1f bet=%.1f stack=%.1f sims=%d line=%s profile=%s",
        mode, stage, position,
        hand, board, players, pot, bet, stack,
        num_simulations, line, player_profile,
    )

    try:
        texture         = analyze_board_texture(board)
        hand_class      = classify_hero_hand(hand, board)
        blockers        = calculate_blocker_score(hand, board, texture)
        range_advantage = estimate_range_advantage(position, board, stage, texture)
        spr             = calculate_spr(eff_stack, pot)   # effective stack, not hero stack
        # Pot odds on hero's REAL price: an oversized bet is only callable up
        # to hero's stack, and the uncallable excess isn't in hero's pot.
        odds_pot, odds_bet = effective_call(pot, bet, stack)
        pot_odds        = calculate_pot_odds(odds_pot, odds_bet)

        win_rate = simulate_equity(
            hand, board, players, position, num_simulations, stage, texture,
            is_3bet_pot=is_3bet_pot, villain_position=villain_position,
            first_in=(bet == 0),
        )

        # On a complete board, the nuts hand wins every possible runout by
        # definition — no simulation approximation needed.
        if hand_class == "nuts" and len(board) == 5:
            win_rate = 1.0

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

        # What-if: only in full (non-fast, non-quick) mode
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

        # Sizing category: fast mode only
        sizing_category = get_sizing_category(action, spr) if is_fast else None

    except Exception as exc:
        logger.exception("Unhandled engine error: %s", exc)
        return jsonify({"error": "Internal engine error. Check server logs."}), 500

    logger.info(
        "Response | mode=%s wr=%.4f ev_c=%.2f ev_r=%.2f "
        "class=%s spr=%.2f conf=%.2f action=%s tags=%s",
        mode, win_rate, ev_call, ev_raise,
        hand_class, spr, confidence, action, tags,
    )

    response = {
        # ── Core outputs ──────────────────────────────────────────────────
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
        # ── Coach outputs ─────────────────────────────────────────────────
        "decision_tags":         tags,
        "reasoning":             reasoning,
        "ux_signals":            ux_signals,
        "player_profile":        player_profile,
        "population_adjustment": population_adj,
        "what_if":               what_if,
        # ── Fast mode extras ──────────────────────────────────────────────
        "sizing_category":  sizing_category,
    }

    return jsonify(response)


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------
@api_bp.route("/health", methods=["GET"])
def health_check():
    """Liveness + DB connectivity check used by hosting platforms."""
    from models import db
    try:
        db.session.execute(db.text("SELECT 1"))
        db.session.commit()
        return jsonify({"status": "ok"}), 200
    except Exception as exc:
        get_logger().error("Health check — DB unreachable: %s", exc)
        return jsonify({"status": "db_error"}), 503
