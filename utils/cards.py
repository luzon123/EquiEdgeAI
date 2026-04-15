"""
Card utility helpers: normalization, deck generation, and stage detection.
"""
from __future__ import annotations

from config import RANKS, SUITS


def normalize_card(card_str: str) -> str:
    if len(card_str) != 2:
        raise ValueError(f"Invalid card string: '{card_str}' (expected 2 chars)")
    return card_str[0].upper() + card_str[1].lower()


def get_full_deck() -> list:
    return [f"{rank}{suit}" for rank in RANKS for suit in SUITS]


def detect_stage(board: list) -> str:
    stages = {0: "preflop", 3: "flop", 4: "turn", 5: "river"}
    n = len(board)
    if n not in stages:
        raise ValueError(f"Invalid board length {n}; must be 0, 3, 4, or 5.")
    return stages[n]
