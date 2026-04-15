"""
Board texture analysis.
"""
from __future__ import annotations

from config import RANK_ORDER


def analyze_board_texture(board: list) -> dict:
    if not board:
        return {
            "wetness": 0.5, "flush_draw": False, "straight_draw": False,
            "paired": False, "monotone": False,
            "high_card_board": False, "low_card_board": False,
            "dry_board": False,
        }

    suits_on_board = [c[1] for c in board]
    ranks_on_board = [c[0] for c in board]
    rank_indices   = sorted([RANK_ORDER[r] for r in ranks_on_board])

    flush_draw    = any(suits_on_board.count(s) >= 3 for s in set(suits_on_board))
    monotone      = len(set(suits_on_board)) == 1 and len(board) >= 3
    paired        = len(ranks_on_board) != len(set(ranks_on_board))

    # Straight-draw: require 3+ board cards to fall within any 5-consecutive-rank
    # window.  The previous "any two cards within 4 ranks" check fired on almost
    # every board (e.g. A-K-2 triggered because A and K are 1 apart), bloating
    # wetness and killing the dry_board flag on disconnected boards.
    rank_val_set = set(14 - ri for ri in rank_indices)   # RANK_ORDER → poker values
    if 14 in rank_val_set:
        rank_val_set.add(1)                               # Ace plays low for wheel
    straight_draw = any(
        len(set(range(lo, lo + 5)) & rank_val_set) >= 3
        for lo in range(1, 11)                            # windows A-5 through T-A
    )

    high_card_board = min(rank_indices) <= RANK_ORDER["T"]
    low_card_board  = max(rank_indices) >= RANK_ORDER["7"]

    wetness = 0.0
    if flush_draw:    wetness += 0.40
    if monotone:      wetness += 0.20
    if straight_draw: wetness += 0.30
    if paired:        wetness -= 0.10
    wetness = max(0.0, min(1.0, wetness))

    # Dry board: rainbow, disconnected, no pair – bluffs work best
    dry_board = (not flush_draw and not straight_draw and not paired
                 and len(set(suits_on_board)) >= 3)

    return {
        "wetness":         wetness,
        "flush_draw":      flush_draw,
        "straight_draw":   straight_draw,
        "paired":          paired,
        "monotone":        monotone,
        "high_card_board": high_card_board,
        "low_card_board":  low_card_board,
        "dry_board":       dry_board,
    }
