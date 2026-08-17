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

from utils.logging_setup import get_logger
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

        logger.debug("Sending image to Claude vision (%s bytes, model=%s).", len(image_data), model)

        t0 = time.monotonic()
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
            latency = time.monotonic() - t0
            logger.error("Claude vision API error after %.2fs: %s", latency, exc)
            raise

        latency = time.monotonic() - t0
        logger.info("Claude vision API responded in %.2fs.", latency)

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
        t0 = time.monotonic()

        # 1. Call the provider
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

        # 2. Parse JSON from response text
        try:
            raw_dict = extract_json_from_text(raw_text)
        except ValueError as exc:
            logger.error("JSON parse failure: %s", exc)
            return ValidationResult(
                valid=False,
                errors=[f"Failed to parse vision response as JSON: {exc}"],
            )

        # 3. Validate and normalise
        validation_result = validate_game_state(raw_dict)

        total = time.monotonic() - t0
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
