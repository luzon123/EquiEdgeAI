"""
Vision-pipeline and fast-mode adapter tests.

Covers the screenshot → structured-state path (validator translation,
position-label normalisation, duplicate detection, typed game state) and
the fast-mode categorical → numeric adapter.  Run with:

    python -m unittest tests.test_vision_and_adapters -v
"""
from __future__ import annotations

import unittest

from models.vision_result import VisionGameState
from services.fast_mode_adapter import adapt_fast_inputs, get_sizing_category
from vision.validator import validate_game_state


def _clubgg_payload(**overrides):
    """A typical raw dict as produced by the ClubGG vision prompt."""
    payload = {
        "total_pot":       240,
        "active_players":  3,
        "my_position":     "Button",
        "action_required": {"type": "call", "amount": 80},
        "my_hand":         ["Jd", "Jh"],
        "my_stack":        1520,
        "board_cards":     ["As", "Tc", "2d"],
    }
    payload.update(overrides)
    return payload


class ValidatorTranslationTests(unittest.TestCase):
    def test_clubgg_fields_translate_and_validate(self):
        result = validate_game_state(_clubgg_payload())
        self.assertTrue(result.valid)
        self.assertEqual(result.data["pot"], 240)
        self.assertEqual(result.data["player_count"], 3)
        self.assertEqual(result.data["hero_cards"], ["Jd", "Jh"])
        self.assertEqual(result.data["board"], ["As", "Tc", "2d"])
        self.assertEqual(result.data["hero_stack"], 1520)
        self.assertEqual(result.data["call_amount"], 80)

    def test_position_labels_normalised_to_engine_vocabulary(self):
        cases = {
            "Button":      "BTN",
            "Small Blind": "SB",
            "Big Blind":   "BB",
            "UTG":         "UTG",
            "UTG+1":       "UTG+1",
            "HJ":          "HJ",
            "Hijack":      "HJ",
            "CO":          "CO",
            "Cutoff":      "CO",
        }
        for raw, expected in cases.items():
            result = validate_game_state(_clubgg_payload(my_position=raw))
            self.assertEqual(result.data["hero_position"], expected, raw)

    def test_unknown_position_label_warns_not_crashes(self):
        result = validate_game_state(_clubgg_payload(my_position="Seat 4"))
        self.assertTrue(any("position" in w.lower() for w in result.warnings))

    def test_check_action_translates_to_zero_call(self):
        payload = _clubgg_payload(action_required={"type": "check", "amount": 0})
        result = validate_game_state(payload)
        self.assertEqual(result.data["call_amount"], 0)

    def test_duplicate_card_between_hand_and_board_rejected(self):
        payload = _clubgg_payload(my_hand=["As", "Kd"])   # As also on board
        result = validate_game_state(payload)
        self.assertFalse(result.valid)
        self.assertTrue(any("Duplicate" in e for e in result.errors))

    def test_invalid_board_count_rejected(self):
        payload = _clubgg_payload(board_cards=["As", "Tc"])
        result = validate_game_state(payload)
        self.assertFalse(result.valid)

    def test_negative_pot_rejected(self):
        payload = _clubgg_payload(total_pot=-50)
        result = validate_game_state(payload)
        self.assertFalse(result.valid)

    def test_null_hero_cards_is_warning_not_error(self):
        payload = _clubgg_payload(my_hand=None)
        result = validate_game_state(payload)
        self.assertTrue(result.valid)
        self.assertTrue(any("hero_cards" in w for w in result.warnings))


class VisionGameStateTests(unittest.TestCase):
    def test_hero_position_survives_to_typed_state_and_dict(self):
        # Regression: hero position was extracted and validated but silently
        # dropped by VisionGameState — the client could never see it.
        result = validate_game_state(_clubgg_payload(my_position="Button"))
        state  = VisionGameState.from_dict(result.data)
        self.assertEqual(state.hero_position, "BTN")
        self.assertEqual(state.to_dict()["hero_position"], "BTN")

    def test_round_trip_core_fields(self):
        result = validate_game_state(_clubgg_payload())
        state  = VisionGameState.from_dict(result.data)
        d      = state.to_dict()
        self.assertEqual(d["pot"], 240)
        self.assertEqual(d["hero_cards"], ["Jd", "Jh"])
        self.assertEqual(d["board"], ["As", "Tc", "2d"])
        self.assertEqual(d["player_count"], 3)


class FastModeAdapterTests(unittest.TestCase):
    def test_pot_always_includes_the_facing_bet(self):
        for action, fraction in (("small", 0.30), ("medium", 0.50),
                                 ("large", 0.75), ("pot", 1.0)):
            ad = adapt_fast_inputs("deep", action)
            expected_bet = round(6.0 * fraction, 1)
            self.assertAlmostEqual(ad["bet"], expected_bet)
            self.assertAlmostEqual(ad["pot"], round(6.0 + expected_bet, 1))

    def test_check_maps_to_no_bet(self):
        ad = adapt_fast_inputs("medium", "check")
        self.assertEqual(ad["bet"], 0.0)
        self.assertAlmostEqual(ad["pot"], 6.0)

    def test_all_in_bet_equals_stack_and_pot_covers_it(self):
        ad = adapt_fast_inputs("short", "all_in")
        self.assertEqual(ad["bet"], 20.0)
        self.assertAlmostEqual(ad["pot"], 26.0)

    def test_sizing_category_covers_bluffs(self):
        self.assertEqual(get_sizing_category("BLUFF 40", spr=5.0), "medium")
        self.assertEqual(get_sizing_category("RAISE 40", spr=1.5), "jam")
        self.assertIsNone(get_sizing_category("CHECK", spr=5.0))
        self.assertIsNone(get_sizing_category("CALL", spr=5.0))


if __name__ == "__main__":
    unittest.main()
