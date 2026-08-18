"""
Tests for POST /mobile/analyze.

Exercises the route end-to-end through the Flask test client with a stub
vision provider (no real network/API calls — no Anthropic key needed to
run this suite).  Also unit-tests the pure action-label mapper in
isolation.  Run with:

    python -m unittest tests.test_mobile_api -v
"""
from __future__ import annotations

import io
import random
import unittest

from PIL import Image

import routes.mobile as mobile_route
import utils.image_utils as image_utils
from services.mobile_format import mobile_action_label
from vision.validator import ValidationResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _png_bytes(size=(20, 20)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color=(10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


class _StubAnalyzer:
    """Drop-in replacement for VisionAnalyzer — returns a canned result."""
    def __init__(self, result=None, raises=None):
        self._result = result
        self._raises = raises

    def analyze(self, image_data, mime_type):
        if self._raises is not None:
            raise self._raises
        return self._result


def _valid_state(**overrides) -> dict:
    state = {
        "hero_cards": ["Ah", "As"],
        "hero_cards_confidence": 0.95,
        "board": [],
        "board_confidence": 0.9,
        "pot": 1.5,
        "call_amount": 0.0,
        "hero_stack": 100.0,
        "hero_position": "UTG",
        "player_count": 6,
        "overall_confidence": 0.9,
    }
    state.update(overrides)
    return state


def _make_app():
    from app import create_app
    app = create_app()
    app.config["TESTING"] = True
    return app


class MobileAnalyzeRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = _make_app()
        self.client = self.app.test_client()
        self._orig_analyzer = mobile_route._analyzer
        self._orig_debug = mobile_route._DEBUG_TIMING

    def tearDown(self):
        mobile_route._analyzer = self._orig_analyzer
        mobile_route._DEBUG_TIMING = self._orig_debug

    def _post(self, image_bytes=None, filename="table.png"):
        data = {}
        if image_bytes is not None:
            data["image"] = (io.BytesIO(image_bytes), filename)
        return self.client.post(
            "/mobile/analyze", data=data, content_type="multipart/form-data"
        )

    # -- input validation -----------------------------------------------------

    def test_missing_image_field_returns_400(self):
        r = self.client.post("/mobile/analyze", data={}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.get_json())

    def test_empty_filename_returns_400(self):
        r = self._post(image_bytes=b"", filename="")
        self.assertEqual(r.status_code, 400)

    def test_invalid_image_bytes_returns_422(self):
        r = self._post(image_bytes=b"not a real image", filename="table.png")
        self.assertEqual(r.status_code, 422)
        self.assertIn("error", r.get_json())

    def test_oversized_image_returns_422(self):
        # `max_bytes` is a keyword-only default bound at function-definition
        # time (`def validate_and_process_image(..., max_bytes=MAX_IMAGE_BYTES)`),
        # so reassigning the module constant after import never reaches the
        # route (it never passes max_bytes explicitly). Patch the function's
        # actual bound default instead — the same object the route calls.
        fn = image_utils.validate_and_process_image
        orig_kwdefaults = dict(fn.__kwdefaults__)
        fn.__kwdefaults__["max_bytes"] = 100
        try:
            r = self._post(image_bytes=_png_bytes((200, 200)))
        finally:
            fn.__kwdefaults__.update(orig_kwdefaults)
        self.assertEqual(r.status_code, 422)
        self.assertIn("too large", r.get_json()["error"].lower())

    # -- vision-layer failures --------------------------------------------------

    def test_vision_provider_failure_returns_generic_502(self):
        mobile_route._analyzer = _StubAnalyzer(raises=RuntimeError("provider exploded, key=SECRET123"))
        r = self._post(image_bytes=_png_bytes())
        self.assertEqual(r.status_code, 502)
        body = r.get_json()
        self.assertEqual(body, {"error": "Analysis failed. Try again."})
        self.assertNotIn("SECRET123", str(body))

    def test_invalid_game_state_returns_422_generic_message(self):
        mobile_route._analyzer = _StubAnalyzer(
            result=ValidationResult(valid=False, errors=["board must be a list, got str."])
        )
        r = self._post(image_bytes=_png_bytes())
        self.assertEqual(r.status_code, 422)
        body = r.get_json()
        self.assertNotIn("board must be a list", str(body))   # no internal leakage

    def test_low_confidence_state_rejected(self):
        mobile_route._analyzer = _StubAnalyzer(
            result=ValidationResult(valid=True, data=_valid_state(overall_confidence=0.05))
        )
        r = self._post(image_bytes=_png_bytes())
        self.assertEqual(r.status_code, 422)

    def test_missing_hero_position_rejected(self):
        state = _valid_state()
        state.pop("hero_position")
        mobile_route._analyzer = _StubAnalyzer(result=ValidationResult(valid=True, data=state))
        r = self._post(image_bytes=_png_bytes())
        self.assertEqual(r.status_code, 422)

    # -- engine-layer failure -----------------------------------------------------

    def test_engine_failure_returns_generic_500(self):
        mobile_route._analyzer = _StubAnalyzer(result=ValidationResult(valid=True, data=_valid_state()))

        def _boom(**kwargs):
            raise ValueError("internal engine detail that must not leak")

        orig = mobile_route.run_decision_pipeline
        mobile_route.run_decision_pipeline = _boom
        try:
            r = self._post(image_bytes=_png_bytes())
        finally:
            mobile_route.run_decision_pipeline = orig
        self.assertEqual(r.status_code, 500)
        body = r.get_json()
        self.assertNotIn("internal engine detail", str(body))

    # -- success path: minimal schema, no internal leakage -----------------------

    def test_successful_analysis_minimal_schema(self):
        random.seed(11)
        mobile_route._analyzer = _StubAnalyzer(
            result=ValidationResult(valid=True, data=_valid_state())   # AA UTG, no bet
        )
        r = self._post(image_bytes=_png_bytes())
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(set(body.keys()), {"winrate", "action"})
        self.assertIsInstance(body["winrate"], float)
        self.assertTrue(0.0 <= body["winrate"] <= 100.0)
        self.assertIn(body["action"], ("FOLD", "CHECK", "CALL", "BET", "RAISE", "ALL-IN"))
        # AA UTG first-in: engine should open, i.e. BET (no bet was facing hero)
        self.assertEqual(body["action"], "BET")
        self.assertGreater(body["winrate"], 50.0)

    def test_weak_hand_facing_big_bet_folds(self):
        random.seed(22)
        state = _valid_state(
            hero_cards=["7h", "2d"],
            board=["Kc", "9s", "4h", "Jd", "3c"],
            pot=200.0,
            call_amount=180.0,
            hero_stack=400.0,
            hero_position="BB",
            player_count=2,
        )
        mobile_route._analyzer = _StubAnalyzer(result=ValidationResult(valid=True, data=state))
        r = self._post(image_bytes=_png_bytes())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["action"], "FOLD")

    def test_strong_hand_facing_bet_calls_or_raises(self):
        random.seed(33)
        state = _valid_state(
            hero_cards=["Ah", "Ad"],
            board=["Ac", "7s", "2h"],
            pot=40.0,
            call_amount=20.0,
            hero_stack=300.0,
            hero_position="BTN",
            player_count=2,
        )
        mobile_route._analyzer = _StubAnalyzer(result=ValidationResult(valid=True, data=state))
        r = self._post(image_bytes=_png_bytes())
        self.assertEqual(r.status_code, 200)
        self.assertIn(r.get_json()["action"], ("CALL", "RAISE", "ALL-IN"))

    def test_debug_timing_hidden_by_default_and_visible_when_enabled(self):
        random.seed(44)
        mobile_route._analyzer = _StubAnalyzer(result=ValidationResult(valid=True, data=_valid_state()))

        mobile_route._DEBUG_TIMING = False
        r = self._post(image_bytes=_png_bytes())
        self.assertNotIn("_timings", r.get_json())

        mobile_route._DEBUG_TIMING = True
        r = self._post(image_bytes=_png_bytes())
        self.assertIn("_timings", r.get_json())
        timing = r.get_json()["_timings"]
        for key in ("vision_ms", "bridge_ms", "engine_ms", "total_ms"):
            self.assertIn(key, timing)


class MobileActionLabelTests(unittest.TestCase):
    """Direct unit tests of the pure FOLD/CHECK/CALL/BET/RAISE/ALL-IN mapper."""

    def test_fold_check_call_pass_through(self):
        self.assertEqual(mobile_action_label("FOLD", bet=20, stack=100), "FOLD")
        self.assertEqual(mobile_action_label("CHECK", bet=0, stack=100), "CHECK")
        self.assertEqual(mobile_action_label("CALL", bet=20, stack=100), "CALL")

    def test_raise_with_no_bet_facing_is_bet(self):
        self.assertEqual(mobile_action_label("RAISE 15", bet=0, stack=100), "BET")

    def test_raise_into_a_bet_is_raise(self):
        self.assertEqual(mobile_action_label("RAISE 60", bet=20, stack=200), "RAISE")

    def test_bluff_maps_same_as_raise(self):
        self.assertEqual(mobile_action_label("BLUFF 15", bet=0, stack=100), "BET")
        self.assertEqual(mobile_action_label("BLUFF 60", bet=20, stack=200), "RAISE")

    def test_full_stack_commitment_is_all_in(self):
        self.assertEqual(mobile_action_label("RAISE 100", bet=20, stack=100), "ALL-IN")
        self.assertEqual(mobile_action_label("RAISE 100", bet=0, stack=100), "ALL-IN")

    def test_near_full_stack_within_rounding_is_all_in(self):
        self.assertEqual(mobile_action_label("RAISE 99.999999", bet=20, stack=100), "ALL-IN")


if __name__ == "__main__":
    unittest.main()
