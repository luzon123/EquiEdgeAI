"""
Hero hand strength classification (7-tier system).
"""
from __future__ import annotations

from itertools import combinations

from treys import Card, Evaluator

from config import RANK_ORDER

_ALL_CARDS = [r + s for r in "AKQJT98765432" for s in "hdcs"]
_CARD_INT  = {c: Card.new(c) for c in _ALL_CARDS}

# treys Evaluator builds its full lookup table in __init__ (~9 ms) and is
# read-only afterwards — construct once and share across calls/threads.
_EVALUATOR = Evaluator()


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
    evaluator  = _EVALUATOR
    used       = set(hero_hand) | set(board)
    deck       = [c for c in _ALL_CARDS if c not in used]
    board_ints = [_CARD_INT[c] for c in board]
    hero_score = evaluator.evaluate(board_ints, [_CARD_INT[c] for c in hero_hand])

    for opp in combinations(deck, 2):
        try:
            opp_score = evaluator.evaluate(board_ints, [_CARD_INT[c] for c in opp])
        except Exception:
            continue
        if opp_score < hero_score:   # opponent beats hero → not nuts
            return False
    return True


def _poker_values(cards: list) -> set:
    """Rank chars → poker values 2-14; the Ace also counts as 1 (wheel)."""
    vals: set = set()
    for c in cards:
        v = 14 - RANK_ORDER[c[0]]
        vals.add(v)
        if v == 14:
            vals.add(1)
    return vals


def _straight_draw_outs(hand: list, board: list) -> set:
    """
    Return the set of out VALUES that complete a straight for hero.

    A window [lo, lo+4] qualifies when it contains exactly 4 of the combined
    hand+board values, the board alone holds fewer than 4 of them (otherwise
    the draw belongs to the whole table), and hero supplies at least one
    value in the window that the board lacks.  The single missing value in
    each qualifying window is an out; 2+ distinct outs = 8-out draw (OESD or
    double-gutshot), 1 out = gutshot.  A 5-hit window would be a made
    straight, which the treys evaluator intercepts before draw detection.
    """
    hero_v  = _poker_values(hand)
    board_v = _poker_values(board)
    all_v   = hero_v | board_v
    outs: set = set()
    for lo in range(1, 11):                      # windows 1-5 (wheel) … 10-14
        window = set(range(lo, lo + 5))
        hits   = window & all_v
        if len(hits) != 4:
            continue
        if len(window & board_v) >= 4:           # board-only draw — not hero's
            continue
        if not (window & (hero_v - board_v)):    # hero adds nothing new here
            continue
        outs |= (window - hits)
    return outs


def _has_backdoor_run(hand: list, board: list) -> bool:
    """Three consecutive combined values with at least one hero value inside."""
    hero_v = _poker_values(hand)
    all_v  = hero_v | _poker_values(board)
    for lo in range(1, 13):
        run = {lo, lo + 1, lo + 2}
        if run <= all_v and (run & hero_v):
            return True
    return False


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

    evaluator  = _EVALUATOR
    board_ints = [_CARD_INT[c] for c in board]
    try:
        score    = evaluator.evaluate(board_ints, [_CARD_INT[c] for c in hand])
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
        # A flopped/rivered set can genuinely BE the nuts on a dry, unpaired,
        # rainbow board (no boat/flush/straight/higher-trips possible) — verify
        # with is_nuts() instead of always capping pocket-pair sets at near_nuts.
        if r1 == r2:
            return "nuts" if is_nuts(hand, board) else "near_nuts"
        return "strong_made"

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
            # Underpair: a pocket pair below only the TOP board card (e.g. QQ
            # on A-7-2) still beats every second-pair hand and plays as a
            # solid bluff-catcher — medium, not weak.  Below the second
            # distinct board rank it's a genuine weak underpair.
            distinct_board = sorted(set(board_rank_idxs))
            if len(distinct_board) > 1 and paired_idx < distinct_board[1]:
                return "medium_made"
            return "weak_made"  # underpair below two or more board ranks

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

    # On a complete 5-card board there are no future cards — an unmade draw
    # is simply air, not an active draw.  Without this guard, a busted flush
    # or straight draw on the river was being labelled "strong_draw", which
    # led decision logic to (incorrectly) treat a dead hand as having live outs.
    if len(board) == 5:
        return "air"

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

    # 2) Straight draw — window-based detection with HERO PARTICIPATION.
    #    For every 5-rank window (wheel A-5 through broadway T-A) that holds
    #    exactly 4 of the combined hand+board ranks, the missing rank is a
    #    straight out.  Two rules keep this honest:
    #      • the board alone must NOT already hold those 4 ranks (a one-card
    #        board straight draw belongs to every player, not to hero), and
    #      • hero must contribute at least one rank the board doesn't have.
    #    Distinct outs across windows separate draw strength exactly:
    #      2+ out ranks (8 outs)  = open-ended / double-gutshot → strong
    #      1 out rank   (4 outs)  = gutshot (incl. A-high / wheel one-enders)
    straight_out_ranks = _straight_draw_outs(hand, board)

    # 3) Combo draw: flush draw AND open-ended straight draw simultaneously (~15 outs).
    #    This is a semi-bluff powerhouse — closer in equity to a made hand than a bare draw.
    if flush_draw_suit is not None and len(straight_out_ranks) >= 2:
        return "combo_draw"

    # 4) Flush draw (a gutshot on the side upgrades a weak flush draw: ~12 outs)
    if flush_draw_suit is not None:
        if flush_hero_top or straight_out_ranks:
            return "strong_draw"
        return "weak_draw"

    # 5) Straight draw
    if len(straight_out_ranks) >= 2:
        return "strong_draw"
    if len(straight_out_ranks) == 1:
        return "weak_draw"

    # 6) Flop-only backdoor: three consecutive ranks including a hero card
    #    (e.g. 9-8 on 7-high).  On the turn a 3-run has zero direct outs = air.
    if len(board) == 3 and _has_backdoor_run(hand, board):
        return "weak_draw"
    return "air"
