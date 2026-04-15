"""
Hero hand strength classification (7-tier system).
"""
from __future__ import annotations

from itertools import combinations

from treys import Card, Evaluator

from config import RANK_ORDER

_ALL_CARDS = [r + s for r in "AKQJT98765432" for s in "hdcs"]


def is_nuts(hero_hand: list[str], board: list[str]) -> bool:
    """
    Returns True if no legal opponent 2-card hand beats hero on this board.

    Card removal is handled exactly: the candidate deck is the 52-card deck
    minus all cards already committed (hero hole cards + board cards).
    Only combinations drawn from that remaining deck are evaluated, so blocked
    cards (e.g. the last T when three Ts are already on the board/in hand) are
    never part of any opponent holding.

    Uses treys Evaluator (lower score = stronger hand).
    Exits immediately on the first opponent hand that beats hero.
    """
    evaluator  = Evaluator()
    used       = set(hero_hand) | set(board)
    deck       = [c for c in _ALL_CARDS if c not in used]
    board_ints = [Card.new(c) for c in board]
    hero_score = evaluator.evaluate(board_ints, [Card.new(c) for c in hero_hand])

    for opp in combinations(deck, 2):
        try:
            opp_score = evaluator.evaluate(board_ints, [Card.new(c) for c in opp])
        except Exception:
            continue
        if opp_score < hero_score:   # opponent beats hero → not nuts
            return False
    return True


def classify_hero_hand(hand: list, board: list) -> str:
    """
    Returns one of:
        nuts / near_nuts / strong_made / medium_made / weak_made /
        strong_draw / weak_draw / air
    """
    r1, r2 = hand[0][0], hand[1][0]
    s1, s2 = hand[0][1], hand[1][1]

    if len(board) < 3:
        if r1 == r2:
            return "nuts" if r1 in "AK" else ("near_nuts" if r1 in "QJ" else "strong_made")
        if r1 in "AK" and r2 in "AK":
            return "near_nuts"
        if r1 in "AKQJT" and r2 in "AKQJT":
            return "strong_made"
        if s1 == s2:
            return "strong_draw" if abs(RANK_ORDER[r1] - RANK_ORDER[r2]) <= 4 else "weak_draw"
        return "air"

    evaluator  = Evaluator()
    board_ints = [Card.new(c) for c in board]
    try:
        score    = evaluator.evaluate(board_ints, [Card.new(c) for c in hand])
        rank_str = evaluator.class_to_string(evaluator.get_rank_class(score))
    except Exception:
        return "air"

    board_ranks = [c[0] for c in board]
    board_suits = [c[1] for c in board]

    # ── Hands that might be nuts: verify with card-aware check ──────────
    # is_nuts() builds the remaining deck (52 − hero − board) and confirms
    # no legal 2-card opponent holding beats hero on this exact board.
    # Straight Flush / Quads can still be beaten by higher SF / Quads.

    if rank_str in ("Straight Flush", "Royal Flush", "Four of a Kind"):
        if is_nuts(hand, board):
            return "nuts"
        return "near_nuts"

    if rank_str == "Full House":
        if is_nuts(hand, board):
            return "nuts"
        return "near_nuts"

    if rank_str == "Flush":
        if is_nuts(hand, board):
            return "nuts"
        dom_suit = max(set(board_suits), key=board_suits.count)
        # A-flush: only a straight-flush can beat it → near_nuts (is_nuts already
        # returned False, so a SF is possible somewhere — near_nuts is correct).
        # K-flush: only A-flush and any SF beat it → also near_nuts.
        # Q/J-flush: several hands beat it → strong_made.
        if any(c[1] == dom_suit and c[0] in "AK" for c in hand):
            return "near_nuts"
        if any(c[1] == dom_suit and RANK_ORDER[c[0]] <= RANK_ORDER["J"] for c in hand):
            return "strong_made"
        return "medium_made"

    if rank_str == "Straight":
        if is_nuts(hand, board):
            return "nuts"
        return "strong_made"

    if rank_str == "Three of a Kind":
        return "near_nuts" if r1 == r2 else "strong_made"

    if rank_str == "Two Pair":
        board_rank_idxs_sorted = sorted([RANK_ORDER[r] for r in board_ranks])
        # Which board ranks do hero's hole cards actually pair?
        hero_paired_board = sorted([RANK_ORDER[r] for r in (r1, r2) if r in board_ranks])
        if len(hero_paired_board) == 2 and len(board_rank_idxs_sorted) >= 2:
            top_board    = board_rank_idxs_sorted[0]
            second_board = board_rank_idxs_sorted[1]
            if (hero_paired_board[0] == top_board
                    and hero_paired_board[1] == second_board):
                return "strong_made"   # true top-two pair (K-Q on K-Q-x)
            elif hero_paired_board[0] == top_board:
                return "medium_made"   # top pair + a lower board pair (K-2 on K-8-2)
            else:
                return "medium_made"   # neither card pairs the top board rank
        # Pocket pair + board pair, or unusual board configuration
        return "medium_made"

    if rank_str == "Pair":
        board_rank_idxs = sorted([RANK_ORDER[r] for r in board_ranks])

        # Pocket pair: check overpair / underpair before the board-match logic.
        # In the One Pair branch the pocket pair never matches a board rank
        # (that would be trips/quads, handled above), so paired_idx will never
        # equal any board_rank_idx — without this block every overpair falls
        # through to "weak_made".
        if r1 == r2:
            paired_idx    = RANK_ORDER[r1]
            top_board_idx = min(board_rank_idxs)   # smallest index = highest rank
            if paired_idx < top_board_idx:          # overpair to entire board
                # J or better overpair → strong_made; lower → medium_made
                return "strong_made" if paired_idx <= RANK_ORDER["J"] else "medium_made"
            return "weak_made"  # underpair

        # Non-pocket pair: find which hole card paired the board
        paired_rank = None
        if r1 in board_ranks:   paired_rank = r1
        elif r2 in board_ranks: paired_rank = r2

        if paired_rank is None:
            # Neither hole card is on the board — hero plays a board pair as kicker
            return "weak_made"

        paired_idx = RANK_ORDER[paired_rank]

        if paired_idx == min(board_rank_idxs):
            kicker = r2 if r1 == paired_rank else r1
            return "strong_made" if RANK_ORDER[kicker] <= RANK_ORDER["J"] else "medium_made"
        if len(board_rank_idxs) > 1 and paired_idx == board_rank_idxs[1]:
            return "medium_made"
        return "weak_made"

    # Draw detection — compute flush draw and straight draw simultaneously so
    # a combo draw (flush draw + OESD, ≈15 outs) can be caught before the
    # individual draw checks consume it as only one of the two.
    hand_suits = [c[1] for c in hand]
    all_suits  = hand_suits + board_suits

    # 1) Flush draw: 4+ same suit on board+hand with at least 1 hero card
    flush_draw_suit = None
    flush_hero_top  = False
    for suit in set(all_suits):
        if all_suits.count(suit) >= 4 and hand_suits.count(suit) >= 1:
            flush_draw_suit = suit
            hero_suited   = [RANK_ORDER[c[0]] for c in hand  if c[1] == suit]
            board_suited  = [RANK_ORDER[c[0]] for c in board if c[1] == suit]
            all_suited    = hero_suited + board_suited
            flush_hero_top = bool(hero_suited and min(hero_suited) == min(all_suited))
            break

    # 2) Straight draw: longest consecutive run of unique rank indices
    hand_rank_idxs  = [RANK_ORDER[c[0]] for c in hand]
    board_rank_idxs = [RANK_ORDER[c[0]] for c in board]
    all_rank_idxs   = sorted(set(hand_rank_idxs + board_rank_idxs))

    consecutive = best_run = 1
    for i in range(1, len(all_rank_idxs)):
        if all_rank_idxs[i] - all_rank_idxs[i - 1] == 1:
            consecutive += 1
            best_run = max(best_run, consecutive)
        else:
            consecutive = 1
    straight_draw_present = best_run >= 4

    # 3) Combo draw: flush draw AND open-ended straight draw simultaneously (~15 outs).
    #    This is a semi-bluff powerhouse — closer in equity to a made hand than a bare draw.
    if flush_draw_suit is not None and straight_draw_present:
        return "combo_draw"

    # 4) Individual flush draw
    if flush_draw_suit is not None:
        return "strong_draw" if flush_hero_top else "weak_draw"

    # 5) Straight draw
    if best_run >= 4: return "strong_draw"
    if best_run == 3: return "weak_draw"
    return "air"
