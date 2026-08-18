"""
POST /mobile/analyze

Minimal screenshot -> {winrate, action} endpoint for the mobile client.

Reuses the existing vision pipeline (vision/analyzer.py), the vision bridge
(services/vision_bridge.py), and the shared decision pipeline
(services/pipeline.py) — no poker logic and no vision-extraction logic
lives in this route.  It only orchestrates the existing pieces and shrinks
their output to the two fields the mobile UI shows.
"""
from __future__ import annotations

import os
import time

from flask import Blueprint, jsonify, request

from extensions import csrf, limiter
from models.vision_result import VisionGameState
from services.mobile_format import mobile_action_label
from services.pipeline import run_decision_pipeline
from services.vision_bridge import build_decision_params
from utils.image_utils import validate_and_process_image
from utils.logging_setup import get_logger
from vision.analyzer import get_default_analyzer

logger    = get_logger()
mobile_bp = Blueprint("mobile", __name__)

# Module-level analyzer singleton — reuses the vision client across requests.
_analyzer = get_default_analyzer()

# Mobile only ever displays a winrate + one-word action, so it runs in
# "quick" mode (fewer Monte Carlo simulations) rather than "full" — the
# extra precision "full" buys (what-if scenarios, reasoning bullets) is
# never rendered, so paying its latency cost here would be pure waste.
_MOBILE_MODE = "quick"

# Timing breakdown is for server-side debugging only. It is gated behind a
# server env var, NOT a client-supplied query param/header — the client
# must never be able to opt itself into seeing internal timing/engine data.
_DEBUG_TIMING = os.environ.get("MOBILE_DEBUG_TIMING", "").strip().lower() in ("1", "true", "yes")


@mobile_bp.route("/mobile/analyze", methods=["POST"])
@csrf.exempt
@limiter.limit("10 per minute")
def mobile_analyze():
    """
    Request:  multipart/form-data, field "image" (PNG/JPEG screenshot).
    Response 200: {"winrate": <0-100 float>, "action": "<FOLD|CHECK|CALL|BET|RAISE|ALL-IN>"}
    Response 4xx/5xx: {"error": "<short human-readable message>"}

    Never exposes vision output, game state, pot/stack/position, EV
    breakdowns, or stack traces to the client.
    """
    t_start = time.monotonic()
    timings: dict = {}

    # -- 1. Extract uploaded file --------------------------------------------
    if "image" not in request.files or not request.files["image"].filename:
        return jsonify({"error": "No image provided."}), 400

    uploaded = request.files["image"]
    try:
        file_bytes = uploaded.read()
    except Exception:
        logger.exception("Mobile analyze: failed to read uploaded file.")
        return jsonify({"error": "Could not read the uploaded image."}), 400

    # -- 2. Validate / pre-process image --------------------------------------
    t0 = time.monotonic()
    try:
        processed = validate_and_process_image(file_bytes=file_bytes, filename=uploaded.filename)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 422
    except RuntimeError:
        logger.exception("Mobile analyze: image processing misconfigured.")
        return jsonify({"error": "Analysis is temporarily unavailable. Try again shortly."}), 500
    timings["image_prep_ms"] = round((time.monotonic() - t0) * 1000, 1)

    # -- 3. Vision pipeline: provider call + JSON parse + state validation ---
    t0 = time.monotonic()
    try:
        vision_result = _analyzer.analyze(processed.data, processed.mime_type)
    except Exception:
        logger.exception("Mobile analyze: vision pipeline error.")
        return jsonify({"error": "Analysis failed. Try again."}), 502
    timings["vision_ms"] = round((time.monotonic() - t0) * 1000, 1)

    if not vision_result.valid:
        logger.warning(
            "Mobile analyze: invalid game state | errors=%s",
            "; ".join(vision_result.errors),
        )
        return jsonify({"error": "Couldn't read the table clearly. Try a clearer screenshot."}), 422

    game_state = VisionGameState.from_dict(vision_result.data)

    # -- 4. Bridge: typed vision state -> decision-pipeline params ------------
    t0 = time.monotonic()
    params, bridge_error = build_decision_params(game_state.to_dict())
    timings["bridge_ms"] = round((time.monotonic() - t0) * 1000, 1)

    if bridge_error:
        logger.info("Mobile analyze: bridge rejected state | reason=%s", bridge_error)
        return jsonify({"error": bridge_error}), 422

    # -- 5. Decision pipeline: equity -> EV -> decision -----------------------
    t0 = time.monotonic()
    try:
        result = run_decision_pipeline(mode=_MOBILE_MODE, **params)
    except Exception:
        logger.exception("Mobile analyze: decision pipeline error.")
        return jsonify({"error": "Analysis failed. Try again."}), 500
    timings["engine_ms"] = round((time.monotonic() - t0) * 1000, 1)

    timings["total_ms"] = round((time.monotonic() - t_start) * 1000, 1)
    logger.info(
        "Mobile analyze complete | action=%s wr=%.3f total_ms=%.1f "
        "(vision=%.1f engine=%.1f)",
        result["action"], result["win_rate"], timings["total_ms"],
        timings["vision_ms"], timings["engine_ms"],
    )

    response = {
        "winrate": round(result["win_rate"] * 100, 1),
        "action":  mobile_action_label(result["action"], result["_bet"], result["_stack"]),
    }
    if _DEBUG_TIMING:
        response["_timings"] = timings

    return jsonify(response), 200
