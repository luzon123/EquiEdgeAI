"""
Application-wide configuration constants for the Poker Decision Engine.

All hard-coded game parameters, population model values, position ranges,
and hand-tier weights live here so they can be imported by any module without
creating circular dependencies.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Simulation limits
# ---------------------------------------------------------------------------
DEFAULT_SIMULATIONS: int = 5000
MAX_SIMULATIONS: int     = 10_000
MIN_SIMULATIONS: int     = 1_000
QUICK_SIMULATIONS: int   = 500

# ---------------------------------------------------------------------------
# Card representation
# ---------------------------------------------------------------------------
SUITS: list      = ["s", "h", "d", "c"]
RANKS: list      = ["A", "K", "Q", "J", "T", "9", "8", "7", "6", "5", "4", "3", "2"]
VALID_RANKS: set = set(RANKS)
VALID_SUITS: set = set(SUITS)

# Lower index = higher rank  (A=0, K=1, ..., 2=12)
RANK_ORDER: dict = {r: i for i, r in enumerate(RANKS)}

# ---------------------------------------------------------------------------
# Population exploit model
# Calibrated to micro-to-mid-stakes online cash game tendencies.
# These values represent systematic deviations from GTO that are exploitable.
# ---------------------------------------------------------------------------
POPULATION: dict = {
    # River: population under-bluffs and under-calls
    "river_bluff_ratio":       0.30,   # players bluff ~30% as often as GTO-correct
    "river_overcall_factor":   0.88,   # river calls are slightly below GTO

    # Flop/Turn: population over-calls vs aggression
    "flop_overcall_factor":    1.35,   # players call flop ~35% more than optimal
    "turn_overcall_factor":    1.20,   # players call turn ~20% more than optimal

    # Bet-size sensitivity: population over-folds to large bets and under-folds to small bets
    "large_bet_fold_mult":     1.45,   # bets >= 0.75 pot → 45% more folds
    "overbet_fold_mult":       1.80,   # overbets (>= pot) → 80% more folds than baseline
    "small_bet_fold_penalty":  0.65,   # bets <= 0.33 pot → 35% fewer folds

    # Multi-way: population plays much tighter and bluffs far less
    "multiway_bluff_penalty":  0.50,   # multiway bluff frequency cut by 50%
    "multiway_fold_penalty":   0.78,   # multiway fold equity reduction

    # Small-pot calling: population calls too wide in small pots (pot-odds insensitive)
    "small_pot_call_boost":    1.25,   # 25% more calls when pot < threshold
    "small_pot_threshold":     40.0,   # pot size considered "small"

    # Continuation bet: how often population folds to a standard cbet
    "cbet_fold_freq_flop":     0.44,
    "cbet_fold_freq_turn":     0.35,

    # Re-raise (3-bet / jam) frequencies when hero raises
    "reraise_freq_preflop":    0.10,
    "reraise_freq_flop":       0.07,
    "reraise_freq_turn":       0.05,
    "reraise_freq_river":      0.03,
    "reraise_value_pct_river": 0.92,   # 92% of river reraises are value hands

    # Calling station threshold: bet/pot ratio below which wide calls occur
    "sticky_call_threshold":   0.40,   # bets <= 40% pot treated as "sticky" environment
}

# ---------------------------------------------------------------------------
# Position-based preflop ranges
# ---------------------------------------------------------------------------
POSITION_RANGES: dict = {
    "UTG": [
        "AA", "KK", "QQ", "JJ", "TT",
        "AKs", "AKo", "AQs", "AQo", "AJs",
    ],
    "MP": [
        "AA", "KK", "QQ", "JJ", "TT", "99",
        "AKs", "AKo", "AQs", "AQo", "AJs", "AJo", "ATs",
        "KQs",
    ],
    "CO": [
        "AA", "KK", "QQ", "JJ", "TT", "99", "88",
        "AKs", "AKo", "AQs", "AQo", "AJs", "AJo", "ATs", "ATo",
        "KQs", "KQo", "KJs", "QJs", "JTs",
    ],
    "BTN": [
        "AA", "KK", "QQ", "JJ", "TT", "99", "88", "77", "66",
        "AKs", "AKo", "AQs", "AQo", "AJs", "AJo", "ATs", "ATo", "A9s", "A8s",
        "KQs", "KQo", "KJs", "KJo", "KTs",
        "QJs", "QJo", "QTs", "JTs", "T9s", "98s", "87s", "76s",
    ],
    "SB": [
        "AA", "KK", "QQ", "JJ", "TT", "99", "88", "77",
        "AKs", "AKo", "AQs", "AQo", "AJs", "AJo", "ATs", "ATo",
        "KQs", "KQo", "KJs", "KTs",
        "QJs", "JTs", "T9s", "98s",
    ],
    "BB": [
        "AA", "KK", "QQ", "JJ", "TT", "99", "88", "77", "66", "55",
        "AKs", "AKo", "AQs", "AQo", "AJs", "AJo", "ATs", "ATo", "A9s", "A8s", "A7s",
        "KQs", "KQo", "KJs", "KJo", "KTs",
        "QJs", "QJo", "QTs", "JTs", "T9s", "98s", "87s", "76s", "65s",
    ],
}

VALID_POSITIONS: set = set(POSITION_RANGES.keys())

# ---------------------------------------------------------------------------
# Player profiles and request modes (used by exploit engine + validation)
# ---------------------------------------------------------------------------
VALID_PLAYER_PROFILES: set = {"tight", "loose", "fish", "reg"}
VALID_MODES: set            = {"full", "quick"}

# ---------------------------------------------------------------------------
# Fast mode
# ---------------------------------------------------------------------------
FAST_SIMULATIONS: int = 300   # real-time feel; lower than QUICK for speed

VALID_STACK_DEPTHS: set   = {"short", "medium", "deep", "very_deep"}
VALID_FACING_ACTIONS: set = {"check", "small", "medium", "large", "pot", "all_in"}

POSITION_AGGRESSION: dict = {
    # Post-flop aggression index (0 = least aggressive / most OOP).
    # SB acts first every post-flop street → least aggressive despite being
    # a common pre-flop 3-bettor; BB defends wide and has position over SB.
    "UTG": 0.0, "MP": 0.2, "CO": 0.6, "BTN": 1.0, "SB": 0.25, "BB": 0.35,
}

# ---------------------------------------------------------------------------
# Hand tier weights
# ---------------------------------------------------------------------------
HAND_TIER_WEIGHTS: dict = {
    "AA": 1.00, "KK": 1.00, "QQ": 0.95, "JJ": 0.90, "TT": 0.85,
    "AKs": 1.00, "AKo": 0.95,
    "99": 0.80, "88": 0.72,
    "AQs": 0.88, "AQo": 0.82, "AJs": 0.80, "KQs": 0.80, "KQo": 0.74,
    "77": 0.65, "66": 0.60, "55": 0.55,
    "AJo": 0.68, "ATs": 0.72, "ATo": 0.62,
    "A9s": 0.60, "A8s": 0.58, "A7s": 0.52,
    "KJs": 0.68, "KJo": 0.58, "KTs": 0.62,
    "QJs": 0.65, "QJo": 0.55, "QTs": 0.60,
    "JTs": 0.60, "T9s": 0.55, "98s": 0.52,
    "87s": 0.48, "76s": 0.44, "65s": 0.40,
}
DEFAULT_HAND_WEIGHT: float = 0.40

PREMIUM_HANDS: set     = {"AA", "KK", "QQ", "JJ", "AKs", "AKo"}
STRONG_HANDS: set      = {"TT", "99", "AQs", "AQo", "AJs", "KQs", "KQo"}
SPECULATIVE_HANDS: set = {
    "88", "77", "66", "55",
    "AJo", "ATs", "ATo", "A9s", "A8s", "A7s",
    "KJs", "KJo", "KTs", "QJs", "QJo", "QTs",
    "JTs", "T9s", "98s", "87s", "76s", "65s",
}
