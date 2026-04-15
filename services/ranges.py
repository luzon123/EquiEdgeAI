"""
Range combo expansion, weighted pool construction, and range advantage estimation.
"""
from __future__ import annotations

import random
from typing import Optional

from treys import Card, Evaluator

from config import (
    SUITS, RANK_ORDER, POSITION_RANGES,
    HAND_TIER_WEIGHTS, DEFAULT_HAND_WEIGHT,
    PREMIUM_HANDS, STRONG_HANDS, SPECULATIVE_HANDS,
)

# 3-bet/4-bet callers/shippers: essentially premiums + top strong hands only
_3BET_RANGE: frozenset = frozenset(PREMIUM_HANDS | STRONG_HANDS)


def expand_range_combos(hand_str: str) -> list:
    suited_only  = hand_str.endswith("s") and len(hand_str) == 3
    offsuit_only = hand_str.endswith("o") and len(hand_str) == 3
    if suited_only or offsuit_only:
        r1, r2 = hand_str[0], hand_str[1]
    else:
        r1 = hand_str[0]
        r2 = hand_str[1] if len(hand_str) >= 2 else hand_str[0]
    combos: list = []
    if r1 == r2:
        cards = [f"{r1}{s}" for s in SUITS]
        for i in range(len(cards)):
            for j in range(i + 1, len(cards)):
                combos.append((cards[i], cards[j]))
    else:
        for s1 in SUITS:
            for s2 in SUITS:
                if suited_only  and s1 != s2: continue
                if offsuit_only and s1 == s2:  continue
                combos.append((f"{r1}{s1}", f"{r2}{s2}"))
    return combos


def build_range_combo_pool(position: str) -> list:
    pool: list = []
    for hand_str in POSITION_RANGES.get(position, POSITION_RANGES["BTN"]):
        pool.extend(expand_range_combos(hand_str))
    return pool


def _stage_weight_decay(hand_str: str, stage: str) -> float:
    base = HAND_TIER_WEIGHTS.get(hand_str, DEFAULT_HAND_WEIGHT)
    if stage == "preflop":
        return base
    if hand_str in PREMIUM_HANDS:
        decay = {"flop": 1.00, "turn": 0.95, "river": 0.90}
    elif hand_str in STRONG_HANDS:
        decay = {"flop": 0.90, "turn": 0.75, "river": 0.55}
    elif hand_str in SPECULATIVE_HANDS:
        decay = {"flop": 0.70, "turn": 0.40, "river": 0.15}
    else:
        decay = {"flop": 0.60, "turn": 0.35, "river": 0.12}
    return base * decay.get(stage, 1.0)


def _board_connect_weight(combo: tuple, board: list) -> float:
    if not board:
        return 1.0
    c1, c2      = combo
    r1, r2      = c1[0], c2[0]
    s1, s2      = c1[1], c2[1]
    board_ranks = [c[0] for c in board]
    board_suits = [c[1] for c in board]
    board_idxs  = [RANK_ORDER[r] for r in board_ranks]
    top_idx     = min(board_idxs)
    weight      = 1.0

    if r1 == r2:
        pair_idx = RANK_ORDER[r1]
        # Set on the board: very likely still in hand, but 2.0× overcounts —
        # sets are 3 specific combos, not twice the combo probability.
        if pair_idx in board_idxs:  weight *= 1.60
        elif pair_idx < top_idx:    weight *= 1.5
        else:                       weight *= 0.65
    else:
        hits = sum(1 for r in (r1, r2) if r in board_ranks)
        if hits == 2:
            weight *= 1.80
        elif hits == 1:
            paired_r = r1 if r1 in board_ranks else r2
            kicker_r = r2 if r1 in board_ranks else r1
            k_str    = 1.0 - RANK_ORDER[kicker_r] / 13.0
            weight  *= (1.4 + k_str * 0.25) if RANK_ORDER[paired_r] == top_idx else 0.85
        else:
            weight *= 0.50

    # Flush-draw board: boost combos that share the dominant suit.
    # Guard with count >= 3 (genuine flush draw) to avoid firing on a paired
    # board that happens to have two cards of the same suit but no draw present.
    for suit in set(board_suits):
        if board_suits.count(suit) >= 3 and (s1 == suit or s2 == suit):
            weight *= 1.20
            break

    hand_idxs = [RANK_ORDER[r1], RANK_ORDER[r2]]
    for hi in hand_idxs:
        for bi in board_idxs:
            if abs(hi - bi) <= 2:
                weight = max(weight, weight * 1.10)
                break

    return max(0.05, min(2.0, weight))


def _board_strength_weight(combo: tuple, board: list, evaluator: Evaluator) -> float:
    """
    Weight a combo by how well it connects with the current board using treys.

    An opponent holding a flopped set is far more likely to still be in the
    hand on the turn/river than someone with bottom pair — this function
    encodes that persistence bias.  The result is geometric-meaned with the
    heuristic _board_connect_weight so neither signal dominates alone.

    Returns a float in [0.02, 1.0].
    """
    if len(board) < 3:
        return 1.0
    try:
        board_ints = [Card.new(c) for c in board]
        hand_ints  = [Card.new(c) for c in combo]
        score      = evaluator.evaluate(board_ints, hand_ints)
        rank_class = evaluator.get_rank_class(score)
        # treys rank classes: 1=SF 2=Quads 3=FH 4=Flush 5=Straight 6=Trips
        #                     7=TwoPair 8=OnePair 9=HighCard
        _class_to_w = {
            1: 1.00,   # Straight Flush
            2: 1.00,   # Quads
            3: 0.98,   # Full House
            4: 0.95,   # Flush
            5: 0.90,   # Straight
            6: 0.88,   # Trips
            7: 0.75,   # Two Pair
        }
        if rank_class in _class_to_w:
            return _class_to_w[rank_class]
        if rank_class == 8:  # One Pair — weight by score (lower = better)
            # treys one-pair score range: best ≈ 3326, worst ≈ 6185
            norm = max(0.0, min(1.0, (6185 - score) / (6185 - 3326)))
            return 0.15 + norm * 0.65   # [0.15, 0.80]
        # High Card: treys score range [6186, 7462]
        norm = max(0.0, min(1.0, (7462 - score) / (7462 - 6186)))
        return 0.02 + norm * 0.12       # [0.02, 0.14]
    except Exception:
        return 1.0


def build_weighted_combo_pool(
    position: str,
    board: list,
    stage: str,
    texture: dict,
    hero_cards: list,
    is_3bet_pot: bool = False,
) -> dict:
    hero_set: set  = set(hero_cards)
    board_set: set = set(board)
    weighted_pool: dict = {}

    # Create evaluator once for board-strength weighting (only needed post-flop)
    evaluator = Evaluator() if len(board) >= 3 else None

    for hand_str in POSITION_RANGES.get(position, POSITION_RANGES["BTN"]):
        tier_w = _stage_weight_decay(hand_str, stage)
        # 3-bet/4-bet pots: villain's range collapses to premiums + strong hands.
        # Apply a heavy weight reduction to non-qualifying hands so the equity
        # simulation and range-advantage estimates reflect a tight 3-bet range.
        if is_3bet_pot and hand_str not in _3BET_RANGE:
            tier_w *= 0.10
        for combo in expand_range_combos(hand_str):
            c1, c2 = combo
            if c1 in hero_set or c2 in hero_set: continue
            if c1 in board_set or c2 in board_set: continue
            board_w    = _board_connect_weight(combo, board)
            if evaluator is not None:
                # Geometric mean of heuristic connectivity and actual hand strength;
                # neither signal dominates — connectivity tells us "fits the board",
                # strength tells us "opponent would still be in the hand".
                strength_w = _board_strength_weight(combo, board, evaluator)
                board_w    = (board_w * strength_w) ** 0.5
            flush_mult = 1.15 if (
                (texture.get("flush_draw") or texture.get("monotone")) and c1[1] == c2[1]
            ) else 1.0
            weighted_pool[combo] = max(0.01, tier_w * board_w * flush_mult)

    return weighted_pool


def weighted_deal_opponent_hand(weighted_pool: dict, used_cards: set) -> Optional[tuple]:
    valid   = [c for c in weighted_pool if c[0] not in used_cards and c[1] not in used_cards]
    if not valid:
        return None
    weights = [weighted_pool[c] for c in valid]
    total   = sum(weights)
    if total <= 0:
        return random.choice(valid)
    r = random.random() * total
    cum = 0.0
    for combo, w in zip(valid, weights):
        cum += w
        if r <= cum:
            return combo
    return valid[-1]


def estimate_range_advantage(
    position: str,
    board: list,
    stage: str,
    texture: dict,
) -> float:
    pos_tightness = {
        "UTG": 0.90, "MP": 0.75, "CO": 0.55,
        "BTN": 0.35, "SB": 0.50, "BB": 0.40,
    }.get(position, 0.55)

    # Preflop: no board to analyze, but position still determines range width.
    # Tight (UTG) = stronger average hand → hero advantage vs. wider villains.
    # Wide (BTN/BB) = weaker average hand → hero disadvantage.
    if not board:
        # pos_tightness: 0.90 = UTG (tightest/strongest), 0.35 = BTN (widest).
        # Remap to advantage: tight = positive (strong range), wide = negative.
        preflop_advantage = (pos_tightness - 0.55) * 0.60
        return max(-0.30, min(0.30, preflop_advantage))

    board_rank_idxs = [RANK_ORDER[c[0]] for c in board]
    avg_board_idx   = sum(board_rank_idxs) / len(board_rank_idxs)
    board_lowness   = avg_board_idx / 12.0

    hero_advantage_raw = (pos_tightness * (1.0 - board_lowness)) - ((1.0 - pos_tightness) * board_lowness)
    wetness_penalty    = texture.get("wetness", 0.5) * 0.30

    # High-card boards favour tight ranges; low-card boards favour speculative hands.
    hcb = texture.get("high_card_board", False)
    lcb = texture.get("low_card_board", False)
    if hcb and pos_tightness >= 0.70:    hero_advantage_raw += 0.10
    elif lcb and pos_tightness <= 0.45:  hero_advantage_raw += 0.08

    hero_advantage  = hero_advantage_raw - wetness_penalty
    stage_mult      = {"preflop": 0.50, "flop": 0.70, "turn": 0.90, "river": 1.00}
    hero_advantage *= stage_mult.get(stage, 0.70)
    return max(-1.0, min(1.0, hero_advantage))
