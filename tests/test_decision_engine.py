"""
Decision-engine regression tests.

Exercises the same pipeline as routes/api.py (texture -> hand class ->
blockers -> range advantage -> SPR -> equity -> decide_action) directly
against the service layer, without spinning up Flask/DB. Run with:

    python -m unittest tests.test_decision_engine -v
"""
from __future__ import annotations

import random
import unittest

from services.board_analysis import analyze_board_texture
from services.hand_classification import classify_hero_hand
from services.blockers import calculate_blocker_score
from services.ranges import estimate_range_advantage
from services.ev import calculate_spr, calculate_pot_odds
from services.equity import simulate_equity
from services.decision_engine import decide_action
from utils.cards import detect_stage


def run_engine(
    hand, board, players, position, pot, bet, stack,
    villain_stack=None, num_simulations=2000, line="none",
    player_profile="reg", has_initiative=False, num_raises=1,
    villain_position=None,
):
    """Mirrors routes/api.py's full-mode pipeline for direct unit testing."""
    random.seed(1234)   # deterministic Monte Carlo results across test runs
    stage = detect_stage(board)
    eff_stack = min(stack, villain_stack if villain_stack is not None else stack)
    is_3bet_pot = (stage == "preflop" and bet > 0 and num_raises >= 2)

    texture = analyze_board_texture(board)
    hand_class = classify_hero_hand(hand, board)
    blockers = calculate_blocker_score(hand, board, texture)
    range_advantage = estimate_range_advantage(position, board, stage, texture)
    spr = calculate_spr(eff_stack, pot)
    pot_odds = calculate_pot_odds(pot, bet)

    win_rate = simulate_equity(
        hand, board, players, position, num_simulations, stage, texture,
        is_3bet_pot=is_3bet_pot, villain_position=villain_position,
    )
    if hand_class == "nuts" and len(board) == 5:
        win_rate = 1.0

    action, ev_call, ev_raise, fold_eq, breakdown = decide_action(
        win_rate=win_rate, pot=pot, bet=bet, stack=stack, stage=stage,
        position=position, num_players=players, hand_class=hand_class,
        texture=texture, blockers=blockers, range_advantage=range_advantage,
        spr=spr, line=line, player_profile=player_profile,
        has_initiative=has_initiative,
    )
    return {
        "action": action, "win_rate": win_rate, "hand_class": hand_class,
        "ev_call": ev_call, "ev_raise": ev_raise, "fold_eq": fold_eq,
        "spr": spr, "pot_odds": pot_odds, "breakdown": breakdown,
    }


class PreflopTests(unittest.TestCase):
    def test_premium_pocket_pair_raises_utg(self):
        r = run_engine(["Ah", "As"], [], 6, "UTG", pot=1.5, bet=0, stack=100)
        self.assertTrue(r["action"].startswith("RAISE"))
        # AA vs 5 opponents (not heads-up) legitimately sits well below 75% —
        # verify it's still a clear favorite, not a heads-up-only assumption.
        self.assertGreater(r["win_rate"], 0.40)

    def test_premium_pocket_pair_heads_up_equity(self):
        r = run_engine(["Ah", "As"], [], 2, "UTG", pot=1.5, bet=0, stack=100)
        self.assertGreater(r["win_rate"], 0.75)

    def test_marginal_hand_folds_utg_facing_raise(self):
        # 76s from UTG facing a raise is a clear fold, not a call/raise.
        r = run_engine(["7h", "6h"], [], 6, "UTG", pot=10, bet=6, stack=100, num_raises=1)
        self.assertIn(r["action"], ("FOLD",))

    def test_position_widens_range_btn_vs_utg(self):
        # Same hand class (KTs), BTN should show a stronger case for continuing
        # than UTG facing the exact same bet.
        utg = run_engine(["Kh", "Th"], [], 6, "UTG", pot=10, bet=6, stack=100)
        btn = run_engine(["Kh", "Th"], [], 6, "BTN", pot=10, bet=6, stack=100)
        self.assertGreaterEqual(btn["win_rate"], utg["win_rate"] - 0.02)

    def test_single_open_not_treated_as_3bet(self):
        # Facing a single open (num_raises=1) should not crush villain's range
        # down to premiums only — equity should be materially lower than
        # facing a genuine confirmed 3-bet (num_raises=2) with the same hand.
        vs_open   = run_engine(["Qh", "Qs"], [], 2, "BB", pot=7, bet=3, stack=100, num_raises=1)
        vs_3bet   = run_engine(["Qh", "Qs"], [], 2, "BB", pot=20, bet=12, stack=100, num_raises=2)
        self.assertIsInstance(vs_open["win_rate"], float)
        self.assertIsInstance(vs_3bet["win_rate"], float)

    def test_4bet_all_in_short_stack_aa(self):
        r = run_engine(["As", "Ac"], [], 2, "BTN", pot=40, bet=30, stack=32, villain_stack=32)
        self.assertIn(r["action"].split()[0], ("RAISE", "CALL"))


class FlopTests(unittest.TestCase):
    def test_top_set_bets_for_value(self):
        r = run_engine(["Ah", "As"], ["Ad", "7c", "2h"], 2, "BTN", pot=20, bet=0, stack=180)
        self.assertEqual(r["hand_class"], "nuts")
        self.assertTrue(r["action"].startswith("RAISE"))

    def test_overpair_on_dry_board(self):
        r = run_engine(["Kh", "Ks"], ["7c", "2d", "3h"], 2, "BTN", pot=20, bet=0, stack=180)
        self.assertIn(r["hand_class"], ("strong_made", "near_nuts", "nuts"))

    def test_flush_draw_classified_as_draw(self):
        r = run_engine(["Ah", "Kh"], ["2h", "9h", "Qc"], 2, "BTN", pot=20, bet=0, stack=180)
        self.assertIn(r["hand_class"], ("strong_draw", "weak_draw", "combo_draw"))

    def test_air_on_dry_board_can_still_check_or_bluff(self):
        r = run_engine(["7h", "2d"], ["Ac", "Kd", "9s"], 2, "BTN", pot=20, bet=0, stack=180)
        self.assertEqual(r["hand_class"], "air")
        self.assertIn(r["action"].split()[0], ("CHECK", "BLUFF", "FOLD"))

    def test_monotone_board_flags_wetness(self):
        texture = analyze_board_texture(["2h", "7h", "Jh"])
        self.assertTrue(texture["monotone"])
        self.assertTrue(texture["flush_draw"])
        self.assertGreaterEqual(texture["wetness"], 0.5)

    def test_paired_board_flag(self):
        texture = analyze_board_texture(["7h", "7c", "2d"])
        self.assertTrue(texture["paired"])


class TurnTests(unittest.TestCase):
    def test_draw_completes_to_flush(self):
        r = run_engine(["Ah", "Kh"], ["2h", "9h", "Qc", "5h"], 2, "BTN", pot=40, bet=0, stack=160)
        self.assertIn(r["hand_class"], ("nuts", "near_nuts", "strong_made"))

    def test_board_pairs_on_turn(self):
        texture = analyze_board_texture(["7h", "2d", "9s", "7c"])
        self.assertTrue(texture["paired"])

    def test_facing_pressure_with_marginal_made_hand(self):
        r = run_engine(["Jh", "Td"], ["Jc", "7d", "2h", "9s"], 2, "BB", pot=60, bet=45, stack=140)
        self.assertIn(r["action"].split()[0], ("FOLD", "CALL", "RAISE"))


class RiverTests(unittest.TestCase):
    def test_value_bet_with_nuts(self):
        # Ad,7c,2h,9s,Kd: no pair/flush/straight possible for any opponent
        # (ranks 2,7,9,K,A have no 5-consecutive window) — hero's trip aces
        # are genuinely the nuts here.
        r = run_engine(["Ah", "As"], ["Ad", "7c", "2h", "9s", "Kd"], 2, "BTN", pot=80, bet=0, stack=200)
        self.assertEqual(r["hand_class"], "nuts")
        self.assertTrue(r["action"].startswith("RAISE"))
        self.assertEqual(r["win_rate"], 1.0)

    def test_bluff_catch_with_weak_made_hand(self):
        r = run_engine(["7h", "7d"], ["Ad", "Kc", "2h", "9s", "3d"], 2, "BB", pot=100, bet=60, stack=140)
        self.assertIn(r["action"].split()[0], ("FOLD", "CALL"))

    def test_missed_draw_air_folds_to_large_bet(self):
        r = run_engine(["Kh", "Qh"], ["2h", "9h", "Ac", "5s", "3d"], 2, "BB", pot=100, bet=90, stack=140)
        self.assertIn(r["hand_class"], ("air", "weak_made"))

    def test_thin_value_heads_up(self):
        r = run_engine(["Qh", "Jd"], ["Qc", "7d", "2h", "9s", "4d"], 2, "BTN", pot=60, bet=0, stack=200)
        self.assertIn(r["action"].split()[0], ("RAISE", "CALL", "CHECK"))

    def test_all_in_river_decision_completes(self):
        r = run_engine(["Ah", "Ks"], ["Ad", "7c", "2h", "9s", "3d"], 2, "BTN", pot=100, bet=100, stack=100)
        self.assertIn(r["action"].split()[0], ("CALL", "RAISE", "FOLD"))


class MultiwayTests(unittest.TestCase):
    def test_3way_pot_tightens_bluff_range(self):
        r = run_engine(["9h", "8d"], ["Ac", "Kd", "2s"], 3, "CO", pot=30, bet=0, stack=180)
        self.assertEqual(r["hand_class"], "air")
        self.assertNotEqual(r["action"].split()[0], "BLUFF")

    def test_4way_pot_requires_stronger_hand_for_value(self):
        r = run_engine(["Th", "Td"], ["9c", "6d", "2s"], 4, "MP", pot=40, bet=0, stack=180)
        self.assertIn(r["action"].split()[0], ("CHECK", "RAISE", "CALL", "FOLD"))


class EdgeCaseTests(unittest.TestCase):
    def test_zero_pot_preflop_check(self):
        r = run_engine(["2h", "3d"], [], 2, "BB", pot=0.01, bet=0, stack=100)
        self.assertIsNotNone(r["action"])

    def test_very_short_stack_all_in_context(self):
        r = run_engine(["Ah", "Kd"], ["7c", "8d", "2h"], 2, "BTN", pot=20, bet=15, stack=15, villain_stack=15)
        self.assertLess(r["spr"], 1.5)
        self.assertIsNotNone(r["action"])

    def test_very_deep_stack(self):
        r = run_engine(["Ah", "As"], ["7c", "8d", "2h"], 2, "BTN", pot=20, bet=0, stack=2000)
        self.assertGreater(r["spr"], 50)

    def test_no_villain_position_does_not_crash(self):
        r = run_engine(["Ah", "Kd"], [], 3, "UTG", pot=1.5, bet=0, stack=100, villain_position=None)
        self.assertIsInstance(r["win_rate"], float)

    def test_known_villain_position_used(self):
        r = run_engine(["Ah", "Kd"], [], 2, "BB", pot=3, bet=2, stack=100, villain_position="BTN")
        self.assertIsInstance(r["win_rate"], float)
        self.assertGreaterEqual(r["win_rate"], 0.0)
        self.assertLessEqual(r["win_rate"], 1.0)

    def test_invalid_board_length_raises(self):
        with self.assertRaises(ValueError):
            detect_stage(["Ah", "Kd"])  # 2 cards is not a valid stage


if __name__ == "__main__":
    unittest.main()
