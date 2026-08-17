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
from utils.logging_setup import get_logger
from vision.analyzer import get_default_analyzer

logger          = get_logger()
analyze_image_bp = Blueprint("analyze_image", __name__)

# Module-level analyzer singleton — reuses the OpenAI client across requests
_analyzer = get_default_analyzer()

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
    t0 = time.monotonic()

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

    # -- 3. Run the vision pipeline ------------------------------------------
    try:
        result = _analyzer.analyze(processed.data, processed.mime_type)
    except Exception as exc:
        # Catch-all — the analyzer already logs internally
        logger.exception("Unexpected error in vision analyzer: %s", exc)
        return jsonify({"error": "An unexpected error occurred during image analysis."}), 500

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

    elapsed = time.monotonic() - t0
    logger.info(
        "analyze-image complete | user=%s elapsed=%.2fs confidence=%.2f warnings=%d",
        getattr(request, "remote_addr", "?"),
        elapsed,
        game_state.overall_confidence,
        len(result.warnings),
    )

    return jsonify({
        "success":    True,
        "game_state": game_state.to_dict(),
        "warnings":   result.warnings,
    }), 200
