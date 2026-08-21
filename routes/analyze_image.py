"""
POST /api/analyze-image

Accepts a multipart/form-data upload with a poker table screenshot,
runs it through the vision pipeline, and returns structured game state JSON.

The route never makes poker decisions — it only returns extracted game state.
"""
from __future__ import annotations

import time

from flask import Blueprint, jsonify, request

from extensions import csrf, limiter
from models.vision_result import VisionGameState
from utils.image_utils import validate_and_process_image
from utils.logging_setup import get_logger, new_request_id
from vision.analyzer import get_default_analyzer

logger          = get_logger()
analyze_image_bp = Blueprint("analyze_image", __name__)

# Module-level analyzer singleton — reuses the vision client across requests
_analyzer = get_default_analyzer()


# Latency-audit instrumentation (2026-08-20, extended 2026-08-21 with
# request_id), added to directly compare this route against
# /mobile/analyze — same pattern as routes/mobile.py's _make_perf_logger,
# tagged [PERF][WEBSITE] here since this is the main website's screenshot
# analyzer, not duplicated via import to keep this route's change surface
# independent/low-risk.
def _make_perf_logger(request_start, request_id):
    prev = [request_start]

    def mark(stage, at=None):
        now = at if at is not None else time.perf_counter()
        elapsed_ms = (now - request_start) * 1000
        duration_ms = (now - prev[0]) * 1000
        prev[0] = now
        logger.info(
            "[PERF][WEBSITE] %s request_id=%s elapsed_ms=%.2f duration_ms=%.2f",
            stage, request_id, elapsed_ms, duration_ms,
        )

    return mark

# ---------------------------------------------------------------------------
# POST /api/analyze-image
# ---------------------------------------------------------------------------

@analyze_image_bp.route("/api/analyze-image", methods=["POST"])
@csrf.exempt
@limiter.limit("10 per minute")
def analyze_image():
    """
    Analyse a poker table screenshot.

    Request:
        Content-Type: multipart/form-data
        Field: image — the screenshot file (PNG, JPG, JPEG)

    Response 200:
        {
          "success": true,
          "game_state": { ... },    // VisionGameState fields
          "warnings": [ ... ]       // non-fatal extraction issues
        }

    Response 400 / 422 / 500:
        { "error": "<message>" }
    """
    t0 = time.perf_counter()
    rid = new_request_id()
    perf = _make_perf_logger(t0, rid)
    perf("request_received", at=t0)

    # -- 1. Extract uploaded file --------------------------------------------
    if "image" not in request.files:
        return jsonify({"error": "No image field in request. Send the file as 'image'."}), 400

    uploaded = request.files["image"]
    if not uploaded.filename:
        return jsonify({"error": "Uploaded file has no filename."}), 400

    try:
        file_bytes = uploaded.read()
    except Exception as exc:
        logger.error("Failed to read uploaded file: %s", exc)
        return jsonify({"error": "Could not read the uploaded file."}), 400
    perf("image_received")

    # -- 2. Validate and pre-process image -----------------------------------
    try:
        processed = validate_and_process_image(
            file_bytes = file_bytes,
            filename   = uploaded.filename,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 422
    except RuntimeError as exc:
        # Pillow / OpenAI not installed — server misconfiguration
        logger.error("Image processing runtime error: %s", exc)
        return jsonify({"error": "Server image processing is not configured correctly."}), 500
    perf("image_processing_done")
    logger.info(
        "[PERF][WEBSITE] image_dims w=%d h=%d mime=%s processed_bytes=%d",
        processed.width, processed.height, processed.mime_type, len(processed.data),
    )

    # -- 3. Run the vision pipeline ------------------------------------------
    perf("claude_started")
    try:
        result = _analyzer.analyze(processed.data, processed.mime_type)
    except Exception as exc:
        # Catch-all — the analyzer already logs internally
        logger.exception("Unexpected error in vision analyzer: %s", exc)
        return jsonify({"error": "An unexpected error occurred during image analysis."}), 500
    perf("claude_finished")

    if not result.valid:
        logger.warning(
            "Vision result invalid | errors=%s",
            "; ".join(result.errors),
        )
        return jsonify({
            "error":    "Image analysis produced an invalid game state.",
            "details":  result.errors,
            "warnings": result.warnings,
        }), 422

    # -- 4. Build typed game state and serialise -----------------------------
    game_state = VisionGameState.from_dict(result.data)
    game_state.validation_warnings = result.warnings

    elapsed = time.perf_counter() - t0
    logger.info(
        "analyze-image complete | user=%s elapsed=%.2fs confidence=%.2f warnings=%d",
        getattr(request, "remote_addr", "?"),
        elapsed,
        game_state.overall_confidence,
        len(result.warnings),
    )
    perf("response_sent")

    return jsonify({
        "success":    True,
        "game_state": game_state.to_dict(),
        "warnings":   result.warnings,
    }), 200
