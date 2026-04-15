"""
Monte Carlo equity simulation with weighted opponent range sampling.

On a complete 5-card board there are no future runouts, so equity is computed
exactly by enumerating every legal opponent 2-card combination from the
remaining deck.  This avoids the weighted-range bias that causes large errors
(e.g. AAAKK returning ~80% instead of ~99.9%).
"""
from __future__ import annotations

import random
from itertools import combinations as _combinations
from typing import Optional

from treys import Card, Evaluator

from config import DEFAULT_SIMULATIONS
from utils.cards import get_full_deck
from utils.logging_setup import get_logger
from services.board_analysis import analyze_board_texture
from services.ranges import build_weighted_combo_pool, weighted_deal_opponent_hand

logger = get_logger()


# ---------------------------------------------------------------------------
# Exact river equity (complete board only)
# ---------------------------------------------------------------------------

def _river_equity_exact(hand: list, board: list, num_opponents: int) -> float:
    """
    Compute equity on a complete 5-card board without Monte Carlo sampling.

    Single opponent  → full enumeration of all C(45,2) = 990 legal combos.
    Multiple opponents → uniform random sample (no weighted-range distortion).

    Card removal is exact: the deck excludes all hero cards and all board cards.
    """
    evaluator  = Evaluator()
    used_base  = set(hand + board)
    deck       = [c for c in get_full_deck() if c not in used_base]
    board_ints = [Card.new(c) for c in board]

    try:
        hero_score = evaluator.evaluate(board_ints, [Card.new(c) for c in hand])
    except Exception:
        return 0.5

    if num_opponents == 1:
        # ── Exact enumeration ───────────────────────────────────────────────
        wins = ties = total = 0
        for opp in _combinations(deck, 2):
            try:
                opp_score = evaluator.evaluate(board_ints, [Card.new(c) for c in opp])
            except Exception:
                continue
            if   hero_score < opp_score:  wins += 1
            elif hero_score == opp_score: ties += 1
            total += 1

        logger.debug("River exact | total_combos=%d wins=%d ties=%d", total, wins, ties)
        return (wins + ties * 0.5) / total if total else 0.5

    # ── Multiple opponents: uniform random MC (no future cards, no range bias) ──
    RIVER_SIMS = 5_000
    wins = ties = valid_runs = 0
    needed = 2 * num_opponents

    for _ in range(RIVER_SIMS):
        if needed > len(deck):
            break
        sampled = random.sample(deck, needed)
        opp_scores = []
        ok = True
        for i in range(num_opponents):
            opp = sampled[2 * i : 2 * i + 2]
            try:
                opp_scores.append(
                    evaluator.evaluate(board_ints, [Card.new(c) for c in opp])
                )
            except Exception:
                ok = False
                break
        if not ok:
            continue
        best_opp = min(opp_scores)
        if   hero_score < best_opp:  wins += 1
        elif hero_score == best_opp: ties += 1
        valid_runs += 1

    logger.debug("River MC (multi-opp) | runs=%d wins=%d ties=%d", valid_runs, wins, ties)
    return (wins + ties * 0.5) / valid_runs if valid_runs else 0.5


# ---------------------------------------------------------------------------
# Main simulation entry-point
# ---------------------------------------------------------------------------

def simulate_equity(
    hand: list,
    board: list,
    num_players: int,
    position: str,
    num_simulations: int = DEFAULT_SIMULATIONS,
    stage: str = "preflop",
    texture: Optional[dict] = None,
    is_3bet_pot: bool = False,
) -> float:
    if texture is None:
        texture = analyze_board_texture(board)

    num_opponents = num_players - 1

    # ── Complete board: use exact / uniform computation, not weighted MC ────
    if len(board) == 5:
        return _river_equity_exact(hand, board, num_opponents)

    # ── Incomplete board: weighted Monte Carlo (existing logic) ─────────────
    evaluator     = Evaluator()
    full_deck     = get_full_deck()
    weighted_pool = build_weighted_combo_pool(position, board, stage, texture, hand,
                                              is_3bet_pot=is_3bet_pot)
    base_known: set = set(hand + board)
    wins = ties = valid_runs = 0

    for _ in range(num_simulations):
        used: set = set(base_known)
        opponent_hands: list = []
        sim_ok = True

        for _opp in range(num_opponents):
            opp_hand = weighted_deal_opponent_hand(weighted_pool, used)
            if opp_hand is None:
                sim_ok = False; break
            opponent_hands.append(opp_hand)
            used.add(opp_hand[0]); used.add(opp_hand[1])

        if not sim_ok:
            continue

        remaining    = [c for c in full_deck if c not in used]
        cards_needed = 5 - len(board)
        if cards_needed > len(remaining):
            continue

        full_board = board + random.sample(remaining, cards_needed)
        try:
            board_ints = [Card.new(c) for c in full_board]
            hero_score = evaluator.evaluate(board_ints, [Card.new(c) for c in hand])
            opp_scores = [
                evaluator.evaluate(board_ints, [Card.new(c) for c in opp])
                for opp in opponent_hands
            ]
            best_opp = min(opp_scores) if opp_scores else float("inf")
            if   hero_score < best_opp:  wins += 1
            elif hero_score == best_opp: ties += 1
        except Exception as exc:
            logger.debug("Evaluation skipped: %s", exc)
            continue

        valid_runs += 1

    if valid_runs == 0:
        logger.warning("Zero valid simulation runs; defaulting equity to 0.5.")
        return 0.5

    equity = (wins + ties * 0.5) / valid_runs
    logger.debug("Simulation | %d runs wins=%d ties=%d equity=%.4f",
                 valid_runs, wins, ties, equity)
    return equity
