"""
Vision analyzer — orchestrates the full screenshot → structured JSON pipeline.

Provider pattern allows swapping the underlying vision model without touching
any other module.  Only this file needs to change when adding a new provider.

Usage:
    provider = ClaudeProvider()
    analyzer = VisionAnalyzer(provider)
    result   = analyzer.analyze(image_bytes, mime_type="image/png")
"""
from __future__ import annotations

import base64
import os
import time
from abc import ABC, abstractmethod
from typing import Any

from utils.logging_setup import current_request_id, get_logger
from vision.prompts     import SYSTEM_PROMPT, EXTRACTION_PROMPT
from vision.parser      import extract_json_from_text
from vision.validator   import validate_game_state, ValidationResult

logger = get_logger()

# ---------------------------------------------------------------------------
# Provider interface
# ---------------------------------------------------------------------------

class VisionProvider(ABC):
    """
    Abstract base for all vision backends.

    Implement `analyze_image` in each concrete subclass.
    The method receives raw image bytes and returns the model's text output.
    """

    @abstractmethod
    def analyze_image(self, image_data: bytes, mime_type: str) -> str:
        """
        Send image to the vision backend and return the raw text response.

        Args:
            image_data: Raw bytes of the image file.
            mime_type:  MIME type string, e.g. "image/png".

        Returns:
            The model's text output (expected to be a JSON string).

        Raises:
            RuntimeError: on provider configuration errors.
            TimeoutError: on network/API timeout.
            Exception:    on any other provider-level failure.
        """


# ---------------------------------------------------------------------------
# Claude (Anthropic) provider
# ---------------------------------------------------------------------------

class ClaudeProvider(VisionProvider):
    """
    Concrete vision provider backed by Anthropic's Claude vision API.

    The Anthropic client is instantiated lazily on first use and reused
    for all subsequent requests to minimise latency.

    Required environment variable:
        ANTHROPIC_API_KEY — Anthropic secret key.

    Optional environment variables:
        CLAUDE_VISION_MODEL   — defaults to "claude-opus-4-5"
        CLAUDE_VISION_TIMEOUT — request timeout in seconds (default: 60)
    """

    def __init__(self) -> None:
        self._client: Any = None  # lazily initialised

    # -- internal helpers ----------------------------------------------------

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client

        try:
            import anthropic  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "anthropic package is not installed. "
                "Add 'anthropic>=0.25.0' to requirements.txt."
            ) from exc

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY environment variable is not set."
            )

        self._client = anthropic.Anthropic(api_key=api_key)
        return self._client

    def _model(self) -> str:
        return os.environ.get("CLAUDE_VISION_MODEL", "claude-opus-4-5")

    def _timeout(self) -> float:
        return float(os.environ.get("CLAUDE_VISION_TIMEOUT", "60"))

    # -- VisionProvider interface --------------------------------------------

    def analyze_image(self, image_data: bytes, mime_type: str) -> str:
        client = self._get_client()
        model  = self._model()

        b64 = base64.standard_b64encode(image_data).decode("ascii")
        image_b64_bytes = len(b64)

        logger.debug("Sending image to Claude vision (%s bytes, model=%s).", len(image_data), model)

        # perf_counter(), not monotonic()/time.time() — highest-resolution
        # clock available, used consistently for every duration measurement
        # added in this latency audit (see routes/mobile.py, services/pipeline.py).
        t0 = time.perf_counter()
        try:
            response = client.messages.create(
                model=model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": mime_type,
                                    "data": b64,
                                },
                            },
                            {
                                "type": "text",
                                "text": EXTRACTION_PROMPT,
                            },
                        ],
                    }
                ],
                timeout=self._timeout(),
            )
        except Exception as exc:
            latency = time.perf_counter() - t0
            logger.error("Claude vision API error after %.2fs: %s", latency, exc)
            raise

        latency = time.perf_counter() - t0
        logger.info("Claude vision API responded in %.2fs.", latency)

        # Real usage numbers straight from the API response — not estimated.
        # Guarded: `.usage` is present on every current Anthropic SDK
        # response, but this must never be able to break the actual
        # analysis if a future/older SDK version shapes it differently.
        usage = getattr(response, "usage", None)
        input_tokens  = getattr(usage, "input_tokens", None)  if usage else None
        output_tokens = getattr(usage, "output_tokens", None) if usage else None
        logger.info(
            "[PERF][VISION] claude_api_call request_id=%s model=%s latency_ms=%.2f "
            "image_bytes=%d image_b64_bytes=%d input_tokens=%s output_tokens=%s max_tokens=1024",
            current_request_id(), model, latency * 1000, len(image_data), image_b64_bytes,
            input_tokens, output_tokens,
        )

        return response.content[0].text if response.content else ""


# ---------------------------------------------------------------------------
# Analyzer — orchestrates provider → parse → validate
# ---------------------------------------------------------------------------

class VisionAnalyzer:
    """
    High-level pipeline coordinator.

    Accepts raw image bytes, runs them through the configured provider,
    parses the response, validates the game state, and returns a
    ValidationResult containing the cleaned data (or errors).
    """

    def __init__(self, provider: VisionProvider) -> None:
        self._provider = provider

    def analyze(self, image_data: bytes, mime_type: str) -> ValidationResult:
        """
        Run the full screenshot → structured JSON pipeline.

        Args:
            image_data: Validated, optionally-resized image bytes.
            mime_type:  MIME type of the image ("image/png", "image/jpeg").

        Returns:
            ValidationResult with:
                .valid    — False if the game state contains hard errors.
                .data     — cleaned game-state dict.
                .warnings — list of non-fatal issues.
                .errors   — list of fatal issues.

        Never raises — all exceptions are caught and returned as errors.
        """
        t0 = time.perf_counter()

        # 1. Call the provider
        t_provider_start = time.perf_counter()
        try:
            raw_text = self._provider.analyze_image(image_data, mime_type)
        except TimeoutError as exc:
            logger.error("Vision API timeout: %s", exc)
            return ValidationResult(
                valid=False,
                errors=[f"Vision API timed out: {exc}"],
            )
        except Exception as exc:
            logger.exception("Vision provider error: %s", exc)
            return ValidationResult(
                valid=False,
                errors=[f"Vision provider error: {exc}"],
            )
        t_provider_end = time.perf_counter()

        # 2. Parse JSON from response text
        t_parse_start = time.perf_counter()
        try:
            raw_dict = extract_json_from_text(raw_text)
        except ValueError as exc:
            logger.error("JSON parse failure: %s", exc)
            return ValidationResult(
                valid=False,
                errors=[f"Failed to parse vision response as JSON: {exc}"],
            )
        t_parse_end = time.perf_counter()

        # 3. Validate and normalise
        t_validate_start = time.perf_counter()
        validation_result = validate_game_state(raw_dict)
        t_validate_end = time.perf_counter()

        total = time.perf_counter() - t0
        conf  = raw_dict.get("overall_confidence", 0.0)
        logger.info(
            "Vision pipeline complete | valid=%s warnings=%d errors=%d "
            "overall_confidence=%.2f total_time=%.2fs",
            validation_result.valid,
            len(validation_result.warnings),
            len(validation_result.errors),
            conf,
            total,
        )
        # Sub-breakdown of the "claude_started -> claude_finished" bracket
        # logged by routes/mobile.py — this file is shared by both
        # /mobile/analyze and the desktop /analyze-image route, so it uses
        # its own generic [PERF][VISION] tag rather than a route-specific one.
        logger.info(
            "[PERF][VISION] breakdown request_id=%s provider_call_ms=%.2f json_parse_ms=%.2f "
            "state_validate_ms=%.2f total_ms=%.2f",
            current_request_id(),
            (t_provider_end - t_provider_start) * 1000,
            (t_parse_end - t_parse_start) * 1000,
            (t_validate_end - t_validate_start) * 1000,
            total * 1000,
        )

        return validation_result


# ---------------------------------------------------------------------------
# Module-level singleton factory
# ---------------------------------------------------------------------------

def get_default_analyzer() -> VisionAnalyzer:
    """
    Return a VisionAnalyzer using the Claude (Anthropic) provider.

    Call this once at app startup (or lazily) and reuse the instance
    across requests so the Anthropic client connection is kept alive.
    """
    return VisionAnalyzer(ClaudeProvider())
