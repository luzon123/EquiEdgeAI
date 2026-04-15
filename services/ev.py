"""
EV calculations: pot odds, SPR helpers, fold equity, raise/call EV,
bluff-catch logic, bet sizing, and bluff frequency decision.

Key design principles:
  - All decisions are deterministic: same input → same output, always.
  - IP/OOP awareness is threaded through fold equity, bluff frequency,
    and raise EV so position-dependent spots are handled correctly.
  - Raise EV on non-river streets applies an equity-realization discount
    instead of assuming an immediate showdown when villain calls.
"""
from __future__ import annotations

from typing import Optional

from config import POPULATION, POSITION_AGGRESSION

# ---------------------------------------------------------------------------
# Deterministic bluff gate — replaces random.random() < prob
# ---------------------------------------------------------------------------
BLUFF_THRESHOLD: float = 0.30   # recommend bluff iff computed frequency >= this


# ===========================================================================
# POT ODDS
# ===========================================================================

def calculate_pot_odds(pot: float, bet: float) -> float:
    if bet <= 0: return 0.0
    return bet / (pot + bet)


# ===========================================================================
# SPR (STACK-TO-POT RATIO)
# ===========================================================================

def calculate_spr(stack: float, pot: float) -> float:
    return stack / pot if pot > 0 else 999.0


def spr_aggression_factor(spr: float) -> float:
    if spr <= 1.0:  return 1.60
    if spr <= 3.0:  return 1.30
    if spr <= 6.0:  return 1.00
    if spr <= 12.0: return 0.85
    return 0.70


def spr_commitment_threshold(spr: float, hand_class: str) -> float:
    base_map = {
        "nuts":        0.40, "near_nuts":   0.45,
        "strong_made": 0.52, "medium_made": 0.58,
        "weak_made":   0.65, "strong_draw": 0.48,
        "combo_draw":  0.44,                        # 15 outs → commits like near_nuts
        "weak_draw":   0.58, "air":         0.72,
        "strong": 0.52, "draw": 0.50, "weak": 0.68,
    }
    base = base_map.get(hand_class, 0.58)
    if   spr <= 2.0:  base -= 0.15
    elif spr <= 4.0:  base -= 0.07
    elif spr >= 12.0: base += 0.08
    return max(0.33, min(0.90, base))


# ===========================================================================
# EQUITY REALIZATION
# ===========================================================================

def get_equity_realization(
    stage: str,
    in_position: bool,
    hand_class: str = "medium_made",
) -> float:
    """
    Fraction of raw Monte Carlo equity that converts to actual EV when villain
    calls a raise on non-river streets.

    River is always 1.0 — the hand reaches showdown next.
    Pre-river: discounted for future street uncertainty, OOP disadvantage,
    and hand-type equity-realization tendencies.

    Intuition:
      - Realized equity = 0.5 + (raw_equity - 0.5) × realization_factor
      - Strong made hands realize more equity (harder to bluff off)
      - Draws realize less (may miss; face pressure on future streets)
      - OOP realizes less than IP (faces more pressure; can't control pot size)
    """
    if stage == "river":
        return 1.0

    # Base realization by street × position.
    # Preflop: equity realises near-fully — villain folds or you see a flop
    # where aggressor's range advantage is substantial.
    base = {
        ("preflop", True):  0.95,
        ("preflop", False): 0.88,
        ("flop",    True):  0.75,
        ("flop",    False): 0.65,
        ("turn",    True):  0.90,
        ("turn",    False): 0.80,
    }.get((stage, in_position), 0.75)

    # Hand-class adjustment
    class_adj = {
        "nuts":        +0.08,
        "near_nuts":   +0.06,
        "strong_made": +0.03,
        "medium_made":  0.00,
        "weak_made":   -0.03,
        "strong_draw": -0.05,
        "combo_draw":  -0.02,   # 15 outs realise equity well but still a draw
        "weak_draw":   -0.10,
        "air":         -0.08,
        # legacy aliases
        "strong": +0.03, "draw": -0.05, "weak": -0.03,
    }
    adjustment = class_adj.get(hand_class, 0.0)
    return max(0.45, min(1.0, base + adjustment))


# ===========================================================================
# POPULATION-ADJUSTED FOLD EQUITY
# ===========================================================================

def estimate_fold_equity(
    stage: str,
    num_players: int,
    position: str,
    raise_fraction: float = 0.65,
    texture: Optional[dict] = None,
    pot: float = 100.0,
    in_position: bool = True,
    has_initiative: bool = False,
) -> float:
    """
    Population-calibrated fold equity.

    Incorporates:
        - Street base rates
        - Bet size (sigmoidal + overbet bonus)
        - Position
        - Multi-way geometric decay
        - Board texture (dry boards = more folds)
        - Population systematic tendencies (over-fold large, under-fold small)
        - Small-pot calling station effect
        - IP/OOP: betting in position is more credible → marginally more folds
    """
    if texture is None:
        texture = {}

    stage_base = {
        "preflop": 0.45, "flop": 0.38, "turn": 0.27, "river": 0.14,
    }
    base = stage_base.get(stage, 0.30)

    # Aggressor advantage: the preflop raiser (c-bettor) has a perceived range
    # advantage that makes villain fold 15-20% more often than an uncredentialed bet.
    if has_initiative:
        base *= 1.18

    # Bet-size adjustment with population over-fold/under-fold
    if raise_fraction >= 1.0:
        bet_adj = 0.12 * (raise_fraction - 0.50) * POPULATION["overbet_fold_mult"]
    elif raise_fraction >= 0.75:
        bet_adj = 0.12 * (raise_fraction - 0.50) * POPULATION["large_bet_fold_mult"]
    elif raise_fraction <= 0.33:
        bet_adj = 0.12 * (raise_fraction - 0.50) * POPULATION["small_bet_fold_penalty"]
    else:
        bet_adj = 0.12 * (raise_fraction - 0.50)
    base += bet_adj

    # Board texture
    if texture.get("dry_board"):
        base *= 1.18
    elif texture.get("wetness", 0.5) >= 0.6:
        base *= 0.88

    # Small pot: population calls wider
    if pot <= POPULATION["small_pot_threshold"]:
        base *= (1.0 / POPULATION["small_pot_call_boost"])

    opponents        = max(1, num_players - 1)
    multi_way_factor = (POPULATION["multiway_fold_penalty"] * 0.68) ** (opponents - 1)
    pos_boost        = POSITION_AGGRESSION.get(position, 0.5) * 0.15

    # IP/OOP adjustment: bets from IP are more credible (population folds
    # slightly more vs in-position aggression; OOP bets are viewed as weaker)
    ip_factor = 1.06 if in_position else 0.94

    fold_eq = (base + pos_boost) * multi_way_factor * ip_factor
    return min(0.90, max(0.02, fold_eq))


# ===========================================================================
# MULTI-OUTCOME EV MODEL (fold / call / re-raise)
# ===========================================================================

def calculate_raise_ev(
    win_rate: float,
    pot: float,
    bet: float,
    raise_amount: float,
    stage: str,
    num_players: int,
    position: str,
    hand_class: str = "medium_made",
    texture: Optional[dict] = None,
    fold_eq_mult: float = 1.0,
    in_position: bool = True,
    has_initiative: bool = False,
) -> tuple[float, dict]:
    """
    3-outcome EV model: fold / call / re-raise.

    fold_eq_mult: player-profile multiplier applied to population fold equity.

    Equity realization: on non-river streets, villain calling a raise does NOT
    lead to immediate showdown — there are future streets where realized equity
    diverges from raw equity. We discount via get_equity_realization():
        realized_wr = 0.5 + (win_rate - 0.5) × realization_factor
    River (realization=1.0) is unchanged from the previous formula.

    Returns (ev_float, outcome_breakdown_dict).
    """
    if texture is None:
        texture = {}

    raise_fraction = raise_amount / pot if pot > 0 else 0.65

    # Fold probability
    p_fold = estimate_fold_equity(
        stage, num_players, position, raise_fraction, texture, pot, in_position,
        has_initiative=has_initiative,
    )
    p_fold = max(0.02, min(0.95, p_fold * fold_eq_mult))

    # Re-raise probability
    base_rr = {
        "preflop": POPULATION["reraise_freq_preflop"],
        "flop":    POPULATION["reraise_freq_flop"],
        "turn":    POPULATION["reraise_freq_turn"],
        "river":   POPULATION["reraise_freq_river"],
    }.get(stage, 0.05)
    rr_size_adj = 0.008 * (raise_fraction - 0.65)
    rr_freq     = max(0.0, min(0.18, base_rr + rr_size_adj))
    rr_freq    *= (1 + 0.03 * (num_players - 2))

    p_call = max(0.0, 1.0 - p_fold - rr_freq)

    # EV when villain folds: hero wins the current pot.
    # Convention: `pot` is the TOTAL pot after any villain bet is already committed.
    # (If villain bet 50 into a 100 pot, the caller passes pot=150, bet=50.)
    # Villain's bet is already embedded in `pot`, so there is no `+ bet` term.
    ev_when_fold = pot

    # EV when villain calls — apply equity realization for non-river streets.
    # realized_wr regresses raw equity toward 50% by the unrealized fraction:
    #   realized_wr = 0.5 + (win_rate - 0.5) × realization
    # This models future street uncertainty, OOP disadvantage, and hand-type effects.
    realization  = get_equity_realization(stage, in_position, hand_class)
    realized_wr  = 0.5 + (win_rate - 0.5) * realization
    ev_when_call = (realized_wr * (pot + raise_amount)) - ((1.0 - realized_wr) * raise_amount)

    # EV when reraised
    if hand_class in ("nuts", "near_nuts"):
        jam_pot             = pot + raise_amount * 2.5
        ev_reraise_response = win_rate * jam_pot - (1.0 - win_rate) * raise_amount
    elif hand_class == "strong_made" and stage != "river":
        ev_reraise_response = (
            0.55 * (win_rate * (pot + raise_amount * 2.5) - (1.0 - win_rate) * raise_amount)
            + 0.45 * (-raise_amount)
        )
    else:
        ev_reraise_response = -raise_amount

    total_ev = (p_fold * ev_when_fold) + (p_call * ev_when_call) + (rr_freq * ev_reraise_response)

    breakdown = {
        "p_fold":              round(p_fold,             3),
        "p_call":              round(p_call,             3),
        "p_reraise":           round(rr_freq,            3),
        "ev_fold":             round(ev_when_fold,       2),
        "ev_call":             round(ev_when_call,       2),
        "ev_reraise_response": round(ev_reraise_response,2),
        "realization":         round(realization,        3),
        "realized_wr":         round(realized_wr,        4),
    }
    return total_ev, breakdown


def calculate_call_ev(win_rate: float, pot: float, bet: float) -> float:
    return (win_rate * pot) - ((1.0 - win_rate) * bet)


# ===========================================================================
# BLUFF-CATCH LOGIC (river-specialised)
# ===========================================================================

def evaluate_bluff_catch(
    win_rate: float,
    pot: float,
    bet: float,
    stage: str,
    num_players: int,
    hand_class: str,
    blockers: dict,
    texture: dict,
    range_advantage: float,
    line: str = "none",
) -> tuple[bool, float, str]:
    """
    Evaluate whether hero should call as a bluff-catcher.
    Returns (should_catch, bluff_catch_ev, reason_str).

    The key input to bluff-catch EV is villain's bluff frequency — how often
    their betting range is a bluff.  Previously this used win_rate * adj, which
    is semantically wrong (win_rate is hero's equity, not villain's bluff freq).
    The corrected model estimates villain_bluff_freq from:
      - Street-calibrated population base rates
      - Bet-size (large / polarised bets contain more bluffs)
      - Line tells (aggressive / check-raise lines are value-heavy)
      - Range advantage (villain having range advantage → more value combos)
      - Hero's blockers (blocking their value hands shifts the ratio toward bluffs)
    """
    # Multiway: only disable bluff-catch when 4+ players remain active.
    # In a 3-player hand one player may already be all-in or have folded, leaving
    # an effectively heads-up confrontation that warrants catching bluffs.
    if bet <= 0 or num_players > 3:
        return False, 0.0, ""

    pot_odds     = calculate_pot_odds(pot, bet)
    bet_fraction = bet / pot if pot > 0 else 0.5

    # Population-calibrated base villain bluff frequency (fraction of their
    # betting range that is a bluff at each street).  Micro-stakes players
    # under-bluff relative to GTO; river is the most under-bluffed street.
    base_bluff_freq = {
        "preflop": 0.10,
        "flop":    0.32,
        "turn":    0.25,
        "river":   0.18,
    }.get(stage, 0.22)

    # Bet-size adjustment: large bets are more polarised — both nut-value and
    # air; small bets lean toward thin value (fewer bluffs)
    if bet_fraction >= 1.0:
        size_mult = 1.40     # overbet: highly polarised, more bluffs in range
    elif bet_fraction >= 0.75:
        size_mult = 1.20
    elif bet_fraction <= 0.33:
        size_mult = 0.75     # small bet: mostly thin value, rarely a bluff
    else:
        size_mult = 1.00
    villain_bluff_freq = base_bluff_freq * size_mult

    # Line tells
    if line == "aggressive":
        villain_bluff_freq *= 0.70   # multi-street aggression is usually value
    elif line == "check_raise":
        villain_bluff_freq *= 0.55   # check-raise is strongly value-weighted

    # Range advantage: villain having the range advantage means more of their
    # range is value combos, shifting the bluff/value ratio down
    villain_bluff_freq *= max(0.50, 1.0 - range_advantage * 0.40)

    # Blocker effects: hero blocking villain's nut hands means a larger fraction
    # of villain's betting range must be bluffs
    blocker_score = blockers.get("blocker_score", 0.0)
    blocks_nuts   = blockers.get("blocks_nuts", False)
    blocker_boost = 1.0 + blocker_score * 0.60
    if blocks_nuts:
        blocker_boost *= 1.35
    villain_bluff_freq = min(0.75, villain_bluff_freq * blocker_boost)

    # EV of calling: win the pot when villain is bluffing, lose the bet otherwise
    bluff_catch_ev = (villain_bluff_freq * pot) - ((1.0 - villain_bluff_freq) * bet)

    catchable_classes   = ("weak_made", "medium_made", "weak_draw", "strong_draw", "air")
    is_catch_hand       = hand_class in catchable_classes
    has_blocker_support = blocker_score >= 0.25 or blocks_nuts
    ev_positive         = bluff_catch_ev > 0

    if stage == "river":
        should_catch = is_catch_hand and ev_positive and has_blocker_support
    else:
        # Pre-river: catch if villain bluffs often enough to cover pot odds
        should_catch = is_catch_hand and villain_bluff_freq >= pot_odds * 0.85

    reason = ""
    if should_catch:
        reason = (
            f"Bluff-catch: villain_bluff={villain_bluff_freq:.0%} "
            f"ev={bluff_catch_ev:+.1f} "
            f"{'(nuts blocker) ' if blocks_nuts else ''}"
            f"{'(polarised bet) ' if bet_fraction >= 0.75 and stage == 'river' else ''}"
        ).strip()

    return should_catch, bluff_catch_ev, reason


# ===========================================================================
# BET SIZING
# ===========================================================================

def compute_raise_size(
    pot: float,
    stack: float,
    stage: str,
    hand_class: str,
    texture: dict,
    is_bluff: bool,
    spr: float = 6.0,
    use_overbet: bool = False,
    thin_value: bool = False,
) -> int:
    stage_fractions = {
        "preflop": 2.00, "flop": 0.50, "turn": 0.72, "river": 1.00,
    }
    base = stage_fractions.get(stage, 0.65)

    wetness     = texture.get("wetness", 0.5)
    texture_adj = (wetness - 0.5) * 0.20

    class_adj = {
        "nuts":        +0.25, "near_nuts":   +0.18,
        "strong_made": +0.10, "medium_made": +0.00,
        "weak_made":   -0.08, "strong_draw": -0.05,
        "weak_draw":   -0.12, "air":         -0.15,
        "strong": +0.10, "draw": -0.05, "weak": -0.15,
    }
    hand_adj = class_adj.get(hand_class, 0.0)

    if is_bluff:
        base     = 0.65 if stage == "river" else max(0.40, base - 0.10)
        hand_adj = 0.0

    if   spr <= 3.0:  spr_adj = +0.15
    elif spr <= 6.0:  spr_adj =  0.00
    elif spr <= 12.0: spr_adj = -0.08
    else:             spr_adj = -0.15

    if use_overbet:
        base     = 1.25 if not is_bluff else 1.10
        hand_adj = 0.0

    if thin_value:
        base = max(0.40, base - 0.15)

    if texture.get("dry_board") and not is_bluff and not thin_value:
        base = min(base * 1.10, 1.30)

    fraction   = max(0.25, min(1.75, base + texture_adj + hand_adj + spr_adj))
    raw_amount = pot * fraction
    return max(1, min(round(raw_amount), round(stack)))


# ===========================================================================
# BLUFFING ENGINE — deterministic, IP/OOP aware
# ===========================================================================

def should_bluff(
    stage: str,
    num_players: int,
    position: str,
    texture: dict,
    hand_class: str,
    blockers: dict,
    range_advantage: float,
    spr: float,
    line: str = "none",
    bluff_freq_mult: float = 1.0,
    in_position: bool = True,
    has_initiative: bool = False,
) -> bool:
    """
    Deterministic bluff decision: returns True iff the computed bluff frequency
    meets or exceeds BLUFF_THRESHOLD (0.30).

    Design: same inputs always produce the same output.  The frequency is a
    product of context multipliers — position, board texture, blockers, SPR,
    hand class, line, opponent count, and IP/OOP.  A user wanting a stochastic
    mixed strategy should self-mix at the returned frequency externally.

    IP/OOP: bluffing in position is more effective because
      - hero can see villain check before deciding to bluff
      - villain faces more uncertainty about hero's range when IP
    OOP bluffs are less credible and should be used sparingly.
    """
    # Post-flop: no bluffs into 4+ players (too many callers).
    # Preflop: num_players is the table size, not active opponents — the stealer
    # acts last with only SB/BB left, so the gate doesn't apply.
    if num_players > 3 and stage != "preflop":
        return False

    stage_base = {
        "preflop": 0.32,   # calibrated so BTN/CO steal air passes threshold
        "flop":    0.30,
        "turn":    0.20,
        "river":   0.08,
    }
    base = stage_base.get(stage, 0.12)

    # Aggressor has range credibility → materially higher bluff frequency.
    # A c-bet from the preflop raiser represents a much wider range of strong
    # hands than a donk bet or out-of-position probe, so folds come more easily.
    if has_initiative:
        base *= 1.25

    if line == "aggressive":
        base *= 1.25
    elif line == "passive":
        base *= 0.65
    elif line == "check_raise":
        base *= 1.40

    pos_factor = 0.50 + POSITION_AGGRESSION.get(position, 0.5) * 0.70
    wetness    = texture.get("wetness", 0.5)
    dry_board  = texture.get("dry_board", False)

    board_factor = 1.55 if dry_board else 1.0 + (0.5 - wetness)

    b_score        = blockers.get("blocker_score", 0.0)
    blocks_nuts    = blockers.get("blocks_nuts", False)
    blocker_factor = 1.0 + b_score * 0.80
    if blocks_nuts:
        blocker_factor *= 1.25

    range_factor = 1.0 + range_advantage * 0.50

    # Preflop: SPR is always huge (stack >> pot) — deep-stack discount on post-flop
    # bluffs doesn't apply to preflop steals where we're never committed.
    if stage == "preflop":
        spr_factor = 1.0
    elif spr <= 2.0:  spr_factor = 0.50
    elif spr <= 5.0:  spr_factor = 1.00
    elif spr <= 12.0: spr_factor = 1.20
    else:             spr_factor = 0.80

    class_factors = {
        "strong_draw": 1.40, "combo_draw": 1.60, "weak_draw": 1.10, "air": 0.80,
        "weak_made": 0.55, "medium_made": 0.30, "strong_made": 0.12,
        "draw": 1.30, "weak": 0.90,
    }
    class_factor = class_factors.get(hand_class, 0.80)

    # Preflop: players fold before action reaches the stealer, so the full
    # geometric multi-way penalty is unrealistic — position already captures it.
    if stage == "preflop":
        opp_factor = 1.0
    else:
        opp_factor = POPULATION["multiway_bluff_penalty"] ** (num_players - 2)

    # IP/OOP: bluffing IP is more effective (credible, lower risk, more equity
    # to fall back on); OOP bluffs face more resistance from aggressive opponents
    ip_factor = 1.18 if in_position else 0.82

    final_prob = (
        base * pos_factor * board_factor * blocker_factor
        * range_factor * spr_factor * class_factor * opp_factor * ip_factor
    )
    final_prob *= bluff_freq_mult
    final_prob  = min(0.55, max(0.0, final_prob))

    # Deterministic gate — no randomness
    return final_prob >= BLUFF_THRESHOLD
