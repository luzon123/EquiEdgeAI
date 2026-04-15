"""
Blocker effect calculations.
"""
from __future__ import annotations

from config import RANK_ORDER


def calculate_blocker_score(hand: list, board: list, texture: dict) -> dict:
    board_ranks       = [c[0] for c in board] if board else []
    board_suits       = [c[1] for c in board] if board else []
    hero_ranks        = [c[0] for c in hand]
    score             = 0.0
    blocks_nuts       = False
    blocks_tp         = False
    nut_blocker_count = 0

    # Having an Ace reduces villain's AA / AK-type combos (range blocker),
    # but does NOT mean hero "blocks the nuts" on every board.
    # blocks_nuts is set only when hero specifically blocks the most likely
    # nut hand given the board (e.g. nut-flush blocker on a flush draw board,
    # or holding the top board rank which blocks top-set/top-two).
    if "A" in hero_ranks:
        score += 0.35
        nut_blocker_count += 1
        # Ace blocks_nuts on boards without a flush draw only if board
        # is Ace-high (hero's Ace blocks top pair / top set type nuts)
        if board_ranks and min(board_ranks, key=lambda r: RANK_ORDER[r]) == "A":
            blocks_nuts = True   # Ace-high board — holding an Ace blocks top set
    if "K" in hero_ranks:
        score += 0.15
        nut_blocker_count += 1

    if board_ranks:
        top_board_rank = min(board_ranks, key=lambda r: RANK_ORDER[r])
        if top_board_rank in hero_ranks:
            blocks_tp = True
            score += 0.20
            blocks_nuts = True   # hero holds the top board rank → blocks top set / top two

    if board_suits and (texture.get("flush_draw") or texture.get("monotone")):
        suit_counts = {}
        for s in board_suits:
            suit_counts[s] = suit_counts.get(s, 0) + 1
        dom_suit = max(suit_counts, key=suit_counts.get)
        if suit_counts[dom_suit] >= 2:
            if f"A{dom_suit}" in hand:
                score += 0.30; blocks_nuts = True; nut_blocker_count += 1
            elif f"K{dom_suit}" in hand:
                score += 0.15

    score = min(1.0, score)
    return {
        "blocker_score":     round(score, 3),
        "blocks_nuts":       blocks_nuts,
        "blocks_tp":         blocks_tp,
        "nut_blocker_count": nut_blocker_count,
    }
