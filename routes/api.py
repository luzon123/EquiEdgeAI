"""
API routes: POST /decision and GET /health.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from extensions import csrf, limiter

from config import DEFAULT_SIMULATIONS, MIN_SIMULATIONS, MAX_SIMULATIONS, FAST_SIMULATIONS
from utils.cards import normalize_card
from utils.validation import validate_request, validate_fast_request
from utils.logging_setup import get_logger
from services.pipeline import run_decision_pipeline
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
        villain_stack    = None
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
        villain_stack = data.get("villain_stack")

        if mode == "quick":
            num_simulations = None   # resolved inside the pipeline
        else:
            raw_sims        = data.get("simulations", DEFAULT_SIMULATIONS)
            num_simulations = max(MIN_SIMULATIONS, min(MAX_SIMULATIONS, int(raw_sims)))

    # ── Run the shared decision pipeline ────────────────────────────────────
    try:
        result = run_decision_pipeline(
            hand=hand, board=board, players=players, pot=pot, bet=bet, stack=stack,
            position=position, villain_stack=villain_stack, player_profile=player_profile,
            mode=mode, line=line, has_initiative=has_initiative, num_raises=num_raises,
            villain_position=villain_position, num_simulations=num_simulations,
        )
    except Exception as exc:
        logger.exception("Unhandled engine error: %s", exc)
        return jsonify({"error": "Internal engine error. Check server logs."}), 500

    # Sizing category: fast mode only
    result["sizing_category"] = get_sizing_category(result["action"], result["spr"]) if is_fast else None
    result.pop("_stage", None)
    result.pop("_bet", None)
    result.pop("_stack", None)

    return jsonify(result)


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
