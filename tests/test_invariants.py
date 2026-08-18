"""
Action-legality invariant fuzz tests.

Runs the full decision pipeline over a few hundred randomly generated —
but legal — game states and asserts that the recommendation is always a
legal poker action for that state:

    * bet == 0  →  never CALL, never FOLD (checking is free)
    * bet  > 0  →  never CHECK
    * RAISE/BLUFF sizes: above the legal minimum raise, at most the stack
    * every EV field is finite

Seeded for reproducibility.  Run with:

    python -m unittest tests.test_invariants -v
"""
from __future__ import annotations

import math
import random
import unittest

from services.board_analysis import analyze_board_texture
from services.hand_classification import classify_hero_hand
from services.blockers import calculate_blocker_score
from services.ranges import estimate_range_advantage
from services.equity import simulate_equity
from services.decision_engine import decide_action
from utils.cards import get_full_deck, detect_stage

POSITIONS = ["UTG", "UTG+1", "UTG+2", "MP", "HJ", "CO", "BTN", "SB", "BB"]
PROFILES  = ["reg", "tight", "loose", "fish"]
LINES     = ["none", "passive", "aggressive", "check_raise"]


def _random_state(rng: random.Random) -> dict:
    deck = get_full_deck()
    rng.shuffle(deck)
    n_board = rng.choice([0, 3, 4, 5])
    pot     = round(rng.uniform(5, 500), 1)
    bet     = 0.0 if rng.random() < 0.4 else round(rng.uniform(1, pot * 1.5), 1)
    return {
        "hand":     deck[:2],
        "board":    deck[2 : 2 + n_board],
        "players":  rng.randint(2, 6),
        "position": rng.choice(POSITIONS),
        "pot":      pot,
        "bet":      bet,
        "stack":    round(rng.uniform(10, 1000), 1),
        "profile":  rng.choice(PROFILES),
        "line":     rng.choice(LINES),
        "init":     rng.random() < 0.5,
    }


class ActionLegalityInvariants(unittest.TestCase):
    N_CASES = 250

    def test_every_recommendation_is_a_legal_action(self):
        rng = random.Random(20260818)
        for i in range(self.N_CASES):
            s = _random_state(rng)
            pot, bet = s["pot"], s["bet"]
            if bet > pot:                      # api.py convention normalisation
                pot += bet
            stage    = detect_stage(s["board"])
            texture  = analyze_board_texture(s["board"])
            hc       = classify_hero_hand(s["hand"], s["board"])
            blockers = calculate_blocker_score(s["hand"], s["board"], texture)
            radv     = estimate_range_advantage(s["position"], s["board"], stage, texture)
            spr      = s["stack"] / pot if pot > 0 else 999.0

            random.seed(i)                     # deterministic MC per case
            wr = simulate_equity(s["hand"], s["board"], s["players"],
                                 s["position"], 200, stage, texture)

            action, ev_c, ev_r, fold_eq, bd = decide_action(
                win_rate=wr, pot=pot, bet=bet, stack=s["stack"], stage=stage,
                position=s["position"], num_players=s["players"], hand_class=hc,
                texture=texture, blockers=blockers, range_advantage=radv,
                spr=spr, line=s["line"], player_profile=s["profile"],
                has_initiative=s["init"],
            )

            ctx = f"case {i}: {s} stage={stage} class={hc} wr={wr:.3f} -> {action}"
            parts = action.split()
            self.assertIn(parts[0], ("FOLD", "CHECK", "CALL", "RAISE", "BLUFF"), ctx)

            if bet == 0:
                self.assertNotIn(parts[0], ("CALL", "FOLD"), ctx)
            else:
                self.assertNotEqual(parts[0], "CHECK", ctx)

            if parts[0] in ("RAISE", "BLUFF"):
                amount    = int(parts[1])
                stack_i   = round(s["stack"])
                bet_eff   = min(bet, s["stack"])
                self.assertGreaterEqual(amount, 1, ctx)
                self.assertLessEqual(amount, stack_i, ctx)
                if bet > 0:
                    # A raise must exceed the callable bet and reach the legal
                    # minimum (2x bet) unless that exceeds the stack (jam).
                    self.assertGreater(amount, bet_eff, ctx)
                    self.assertGreaterEqual(
                        amount, min(round(2 * bet_eff), stack_i), ctx)

            for v in (ev_c, ev_r, fold_eq):
                self.assertTrue(math.isfinite(v), ctx)
            self.assertTrue(0.0 <= fold_eq <= 1.0, ctx)


if __name__ == "__main__":
    unittest.main()
