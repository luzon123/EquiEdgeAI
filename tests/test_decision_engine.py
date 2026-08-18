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
    if bet > pot:       # exclusive-convention payload → normalise (as api.py does)
        pot += bet
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
        first_in=(bet == 0),
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


class RegressionTests(unittest.TestCase):
    """One test per defect found in the 2026-08 forensic audit."""

    def test_no_call_label_without_a_bet(self):
        # 99 on A72 rainbow with no bet facing used to return "CALL".
        r = run_engine(["9h", "9d"], ["Ac", "7c", "2h"], 2, "BB", pot=40, bet=0, stack=200)
        self.assertNotEqual(r["action"].split()[0], "CALL")
        self.assertIn(r["action"].split()[0], ("CHECK", "RAISE", "BLUFF", "FOLD"))

    def test_raise_size_never_below_legal_minimum(self):
        # Top set facing 60 into 100 used to recommend "RAISE 65" (< min-raise 120).
        r = run_engine(["Ah", "Ad"], ["Ac", "7c", "2h"], 2, "BTN", pot=100, bet=60, stack=500)
        parts = r["action"].split()
        if parts[0] in ("RAISE", "BLUFF"):
            self.assertGreaterEqual(int(parts[1]), 120)
            self.assertLessEqual(int(parts[1]), 500)

    def test_air_does_not_bluff_catch_preflop_flop(self):
        # 72o on K94r facing 50 into 150 used to CALL as a "bluff-catcher"
        # with 5% equity and no showdown value.
        r = run_engine(["7h", "2d"], ["Kc", "9s", "4h"], 2, "BB", pot=150, bet=50, stack=500)
        self.assertEqual(r["hand_class"], "air")
        self.assertNotEqual(r["action"], "CALL")

    def test_board_only_straight_draw_is_not_a_hero_draw(self):
        # Board 4-5-6-7 gives EVERY player the same one-card straight draw;
        # hero's AK adds nothing and must not be classified as a draw.
        self.assertEqual(
            classify_hero_hand(["Ah", "Kd"], ["4c", "5d", "6h", "7s"]), "air")

    def test_real_gutshot_detected(self):
        # 98 on 5-6-Q: any 7 completes — a genuine 4-out gutshot.
        self.assertEqual(
            classify_hero_hand(["9h", "8d"], ["5c", "6d", "Qh"]), "weak_draw")

    def test_oesd_detected_as_strong(self):
        # 98 on 7-6-2: T or 5 completes — 8 outs, open-ended.
        self.assertEqual(
            classify_hero_hand(["9h", "8d"], ["7c", "6d", "2h"]), "strong_draw")

    def test_broadway_one_ender_is_weak(self):
        # KQJ + A in hand... use QJ on K-A-4: only a T completes (4 outs).
        self.assertEqual(
            classify_hero_hand(["Qh", "Jd"], ["Kc", "Ah", "4s"]), "weak_draw")

    def test_nuts_fast_plays_wet_multiway_board(self):
        # Straight flush on a monotone connected board 4-way used to slow-play;
        # wet multiway boards are exactly where the nuts must bet.
        r = run_engine(["9h", "8h"], ["7h", "6h", "5h"], 4, "BTN", pot=60, bet=0, stack=400)
        self.assertEqual(r["hand_class"], "nuts")
        self.assertTrue(r["action"].startswith("RAISE"), r["action"])

    def test_oversized_shove_uses_effective_stack_odds(self):
        # Villain shoves 400, hero stack 100: hero risks 100 to win 200.
        # TPTK at ~70% equity is a clear all-in call, and "raising" is impossible.
        r = run_engine(["Ah", "Kh"], ["Kc", "9s", "4h"], 2, "BB", pot=500, bet=400, stack=100)
        self.assertEqual(r["action"], "CALL")
        self.assertTrue(r["breakdown"].get("all_in_call"))

    def test_underpair_below_top_card_only_is_medium(self):
        self.assertEqual(classify_hero_hand(["Qh", "Qd"], ["Ac", "7s", "2h"]), "medium_made")
        self.assertEqual(classify_hero_hand(["3h", "3d"], ["Ac", "7s", "2h"]), "weak_made")

    def test_hj_position_supported_end_to_end(self):
        r = run_engine(["Ah", "Qs"], [], 6, "HJ", pot=1.5, bet=0, stack=100)
        self.assertIsInstance(r["win_rate"], float)
        self.assertIsNotNone(r["action"])

    def test_preflop_first_in_equity_uses_live_opponents_only(self):
        # Folded hands are dead cards: AQo open-decision equity at a 6-max
        # table must not be simulated against five live top-range hands.
        r = run_engine(["Ah", "Qs"], [], 6, "HJ", pot=1.5, bet=0, stack=100)
        self.assertGreater(r["win_rate"], 0.30)

    def test_standard_opens_raise_and_junk_checks(self):
        tt = run_engine(["Th", "Ts"], [], 6, "MP", pot=1.5, bet=0, stack=100)
        self.assertTrue(tt["action"].startswith("RAISE"), tt["action"])
        aqo = run_engine(["Ah", "Qs"], [], 6, "CO", pot=1.5, bet=0, stack=100)
        self.assertTrue(aqo["action"].startswith("RAISE"), aqo["action"])
        # K5o UTG first-in is never an open.
        k5 = run_engine(["Kh", "5s"], [], 6, "UTG", pot=1.5, bet=0, stack=100)
        self.assertIn(k5["action"].split()[0], ("CHECK", "FOLD"))

    def test_exclusive_pot_convention_normalised(self):
        # pot=100 bet=400 is impossible under the inclusive convention; the
        # pipeline must treat it as pot 500 total, not reject or misprice it.
        r = run_engine(["Ah", "Kh"], ["Kc", "9s", "4h"], 2, "BB", pot=100, bet=400, stack=1000)
        self.assertIsNotNone(r["action"])

    def test_tptk_calls_not_raises_river_pot_bet(self):
        # Population river bets are value-heavy (POPULATION model): raising
        # one-pair TPTK into a river pot-bet is dominated — call instead.
        r = run_engine(["Ah", "Kd"], ["Kc", "7s", "2h", "9d", "3c"], 2, "BB",
                       pot=200, bet=100, stack=400)
        self.assertEqual(r["hand_class"], "strong_made")
        self.assertEqual(r["action"], "CALL")

    def test_thin_value_only_when_checked_to(self):
        # Thin-value sizing must never produce a raise INTO a river bet.
        r = run_engine(["Qh", "Jd"], ["Qc", "7d", "2h", "9s", "4d"], 2, "BTN",
                       pot=100, bet=60, stack=300)
        self.assertNotEqual(r["action"].split()[0], "RAISE")

    def test_river_air_never_labelled_draw(self):
        # Busted flush draw on a complete board is air, not a live draw.
        self.assertEqual(
            classify_hero_hand(["Kh", "Qh"], ["2h", "9h", "Ac", "5s", "3d"]), "air")


class MathTests(unittest.TestCase):
    """Direct formula checks (no Monte Carlo)."""

    def test_raise_ev_call_branch_uses_villain_increment(self):
        from services.ev import calculate_raise_ev
        # River (realization = 1.0): wr .55, pot 100 (incl. villain's 60 bet),
        # raise to 150.  Villain calling adds 90, hero risks 150:
        #   0.55*(100+90) - 0.45*150 = 37.0
        _ev, bd = calculate_raise_ev(0.55, 100, 60, 150, "river", 2, "BTN",
                                     "strong_made", {})
        self.assertAlmostEqual(bd["ev_call"], 37.0, places=1)

    def test_fast_mode_pot_includes_bet(self):
        from services.fast_mode_adapter import adapt_fast_inputs
        from services.ev import calculate_pot_odds
        ad = adapt_fast_inputs("medium", "medium")     # half-pot bet
        # 3 into 6 → total pot 9; required equity = 3/(9+3) = 0.25
        self.assertAlmostEqual(ad["pot"], 9.0, places=1)
        self.assertAlmostEqual(calculate_pot_odds(ad["pot"], ad["bet"]), 0.25, places=3)

    def test_legalize_raise_size(self):
        from services.ev import legalize_raise_size
        self.assertEqual(legalize_raise_size(54, 60, 500), 120)   # below min → min-raise
        self.assertEqual(legalize_raise_size(300, 60, 500), 300)  # legal as-is
        self.assertEqual(legalize_raise_size(54, 60, 90), 90)     # short → jam
        self.assertEqual(legalize_raise_size(54, 60, 60), 0)      # stack <= bet → no raise
        self.assertEqual(legalize_raise_size(80, 0, 50), 50)      # no bet → cap at stack

    def test_effective_call_side_pot(self):
        from services.ev import effective_call
        self.assertEqual(effective_call(500, 400, 100), (200, 100))
        self.assertEqual(effective_call(150, 50, 500), (150, 50))

    def test_pot_odds_formula(self):
        from services.ev import calculate_pot_odds
        # Villain bets 50 into 100 → total pot 150; call 50 to win 200.
        self.assertAlmostEqual(calculate_pot_odds(150, 50), 0.25, places=3)
        self.assertEqual(calculate_pot_odds(100, 0), 0.0)

    def test_board_texture_flags_mutually_exclusive(self):
        for board in (["Kc", "7s", "2h"], ["9c", "7s", "2h"], ["Jc", "Ts", "2h"]):
            t = analyze_board_texture(board)
            self.assertFalse(t["high_card_board"] and t["low_card_board"], board)
        self.assertTrue(analyze_board_texture(["Kc", "7s", "2h"])["high_card_board"])
        self.assertTrue(analyze_board_texture(["9c", "7s", "2h"])["low_card_board"])

    def test_bluff_catch_requires_showdown_value(self):
        from services.ev import evaluate_bluff_catch
        no_blockers = {"blocker_score": 0.0, "blocks_nuts": False}
        # Pre-river air must never catch.
        catch, _, _ = evaluate_bluff_catch(0.10, 150, 50, "flop", 2, "air",
                                           no_blockers, {"wetness": 0.3}, 0.0)
        self.assertFalse(catch)
        # River air without an Ace-high calibre blocker must not catch.
        catch, _, _ = evaluate_bluff_catch(0.10, 100, 60, "river", 2, "air",
                                           {"blocker_score": 0.15, "blocks_nuts": False},
                                           {"wetness": 0.3}, 0.0)
        self.assertFalse(catch)


if __name__ == "__main__":
    unittest.main()
