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

from flask import Blueprint, current_app, jsonify, render_template, request, send_from_directory

from extensions import csrf, limiter
from models.vision_result import VisionGameState
from services.mobile_format import mobile_action_label
from services.pipeline import run_decision_pipeline
from services.vision_bridge import build_decision_params
from utils.image_utils import validate_and_process_image
from utils.logging_setup import get_logger, new_request_id
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


def _make_perf_logger(request_start, request_id):
    """
    Latency-audit instrumentation only — does not affect behavior or the
    response contract. Returns a `mark(stage, at=None)` closure, request-
    local (a fresh one per call to mobile_analyze(), so concurrent Gunicorn
    threads never share/corrupt each other's "previous stage" pointer).

    `at`, when given, lets a stage that already happened (e.g. equity/
    decision timestamps returned from services/pipeline.py) be logged with
    its real perf_counter() timestamp rather than "now" — perf_counter()
    values are only ever compared within this same process, which is valid
    here since request_start was also captured with perf_counter().

    request_id (added for the 2026-08-21 /mobile vs /api/analyze-image
    latency investigation): lets one request be grepped end-to-end out of
    interleaved production logs, and correlated by timestamp against the
    Anthropic SDK's own retry log line (logger "anthropic._base_client",
    "Retrying request to %s in %f seconds" — emitted automatically by the
    installed SDK on 408/409/429/5xx, already visible today under this
    project's existing logging.basicConfig(level=INFO), no change needed
    for that specific line to appear).
    """
    prev = [request_start]

    def mark(stage, at=None):
        now = at if at is not None else time.perf_counter()
        elapsed_ms = (now - request_start) * 1000
        duration_ms = (now - prev[0]) * 1000
        prev[0] = now
        logger.info(
            "[PERF][MOBILE] %s request_id=%s elapsed_ms=%.2f duration_ms=%.2f",
            stage, request_id, elapsed_ms, duration_ms,
        )

    return mark


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
    t_start = time.perf_counter()
    rid = new_request_id()
    perf = _make_perf_logger(t_start, rid)
    timings: dict = {}
    logger.info("[MOBILE] request_received")
    perf("request_received", at=t_start)

    # -- 1. Extract uploaded file --------------------------------------------
    # request.files is accessed here for the FIRST time in this request —
    # this is exactly where Werkzeug lazily reads and parses the entire
    # multipart/form-data body from the socket (Gunicorn's sync worker has
    # already accepted the connection by the time our view function even
    # starts running, but the BODY isn't necessarily fully read off the
    # wire until something touches request.form/request.files). Splitting
    # multipart_parse_started/completed from image_received (after
    # uploaded.read(), which only reads from the ALREADY-buffered
    # FileStorage) isolates "time to receive the upload over the network"
    # from "our own Python code" — added for the 2026-08-21 investigation
    # into the request_received -> image_received gap.
    perf("multipart_parse_started")
    if "image" not in request.files or not request.files["image"].filename:
        return jsonify({"error": "No image provided."}), 400

    uploaded = request.files["image"]
    perf("multipart_parse_completed")

    try:
        file_bytes = uploaded.read()
    except Exception as exc:
        logger.error(
            "[MOBILE][ERROR] stage=read_upload exception=%s: %s",
            type(exc).__name__, exc, exc_info=True,
        )
        return jsonify({"error": "Could not read the uploaded image."}), 400
    logger.info("[MOBILE] image_received | bytes=%d", len(file_bytes))
    perf("image_received")

    # -- 2. Validate / pre-process image --------------------------------------
    t0 = time.perf_counter()
    logger.info("[MOBILE] image_validation_started")
    try:
        processed = validate_and_process_image(file_bytes=file_bytes, filename=uploaded.filename)
    except ValueError as exc:
        # An expected, handled rejection (bad format/size/corrupt file) —
        # not a bug, so logged as a plain rejection rather than [ERROR].
        logger.info("[MOBILE] image_validation_rejected | reason=%s", exc)
        return jsonify({"error": str(exc)}), 422
    except RuntimeError as exc:
        logger.error(
            "[MOBILE][ERROR] stage=image_validation exception=%s: %s",
            type(exc).__name__, exc, exc_info=True,
        )
        return jsonify({"error": "Analysis is temporarily unavailable. Try again shortly."}), 500
    timings["image_prep_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    logger.info(
        "[MOBILE] image_validation_passed | mime=%s ms=%.1f w=%d h=%d",
        processed.mime_type, timings["image_prep_ms"], processed.width, processed.height,
    )
    perf("image_processing_done")

    # -- 3. Vision pipeline: provider call + JSON parse + state validation ---
    # (Claude call + JSON parse + game-state validation happen inside this
    # one call — see vision/analyzer.py's own [PERF][VISION] log for the
    # sub-breakdown between those three. "state_validation_done" below is
    # therefore logged immediately after "claude_finished" with a near-zero
    # duration_ms — validation's real cost is already inside that bracket,
    # not a separately-timed stage at this level; see [PERF][VISION].)
    t0 = time.perf_counter()
    logger.info("[MOBILE] claude_request_started")
    perf("claude_started")
    try:
        vision_result = _analyzer.analyze(processed.data, processed.mime_type)
    except Exception as exc:
        # VisionAnalyzer.analyze() normally catches provider errors itself
        # and returns ValidationResult(valid=False, ...) rather than
        # raising — this branch only fires for a genuinely unexpected bug
        # elsewhere in the pipeline, not an ordinary Claude failure/timeout.
        logger.error(
            "[MOBILE][ERROR] stage=claude_request exception=%s: %s",
            type(exc).__name__, exc, exc_info=True,
        )
        return jsonify({"error": "Analysis failed. Try again."}), 502
    timings["vision_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    logger.info(
        "[MOBILE] claude_response_received | ms=%.1f valid=%s",
        timings["vision_ms"], vision_result.valid,
    )
    perf("claude_finished")

    if not vision_result.valid:
        logger.warning(
            "Mobile analyze: invalid game state | errors=%s",
            "; ".join(vision_result.errors),
        )
        return jsonify({"error": "Couldn't read the table clearly. Try a clearer screenshot."}), 422

    perf("state_validation_done")
    game_state = VisionGameState.from_dict(vision_result.data)

    # -- 4. Bridge: typed vision state -> decision-pipeline params ------------
    t0 = time.perf_counter()
    params, bridge_error = build_decision_params(game_state.to_dict())
    timings["bridge_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    if bridge_error:
        logger.info("Mobile analyze: bridge rejected state | reason=%s", bridge_error)
        return jsonify({"error": bridge_error}), 422

    # -- 5. Decision pipeline: equity -> EV -> decision -----------------------
    t0 = time.perf_counter()
    logger.info("[MOBILE] decision_engine_started")
    try:
        result = run_decision_pipeline(mode=_MOBILE_MODE, **params)
    except Exception as exc:
        logger.error(
            "[MOBILE][ERROR] stage=decision_engine exception=%s: %s",
            type(exc).__name__, exc, exc_info=True,
        )
        return jsonify({"error": "Analysis failed. Try again."}), 500
    timings["engine_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    # Equity/decision sub-stage timestamps computed inside the pipeline
    # itself (services/pipeline.py) — logged here, retroactively, with
    # their real perf_counter() timestamps, and never allowed to reach the
    # response (popped immediately after use).
    pipeline_perf = result.pop("_perf", None)
    if pipeline_perf:
        perf("equity_started", at=pipeline_perf["equity_start"])
        perf("equity_finished", at=pipeline_perf["equity_end"])
        perf("decision_started", at=pipeline_perf["decision_start"])
        perf("decision_finished", at=pipeline_perf["decision_end"])
        timings["equity_ms"] = round((pipeline_perf["equity_end"] - pipeline_perf["equity_start"]) * 1000, 2)
        timings["decision_ms"] = round((pipeline_perf["decision_end"] - pipeline_perf["decision_start"]) * 1000, 2)

    timings["total_ms"] = round((time.perf_counter() - t_start) * 1000, 1)
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

    logger.info("[MOBILE] response_sent | action=%s winrate=%s", response["action"], response["winrate"])
    perf("response_sent")
    return jsonify(response), 200


# ---------------------------------------------------------------------------
# GET /mobile — iPhone PWA (Safari has no cross-app screen capture, so this
# is a manual-screenshot-picker client, not a floating-overlay one like
# Android). Thin HTML/JS shell only; it POSTs to mobile_analyze() above like
# any other client — no separate endpoint, no separate contract.
# ---------------------------------------------------------------------------
@mobile_bp.route("/mobile", methods=["GET"])
def mobile_pwa():
    return render_template("mobile_pwa.html")


# Served at the ROOT path (not /static/pwa/sw.js) so its default scope is
# "/" and it can control /mobile without needing a Service-Worker-Allowed
# response header — a script's own URL path sets the ceiling on the scope
# it can register for.
@mobile_bp.route("/sw.js", methods=["GET"])
def mobile_service_worker():
    return send_from_directory(
        os.path.join(current_app.static_folder, "pwa"),
        "sw.js",
        mimetype="application/javascript",
    )
