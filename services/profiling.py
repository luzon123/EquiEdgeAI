"""
Player profile exploit engine.

Maps a player profile label (tight | loose | fish | reg | default) to
a set of multipliers that are threaded through EV and bluff calculations
to exploit systematic tendencies of that opponent type.
"""
from __future__ import annotations

from services.exploit_engine import PLAYER_PROFILES


def get_profile_multipliers(player_profile: str) -> dict:
    """
    Return exploit multipliers for the given player profile.

    Keys in returned dict:
        fold_equity_mult  — scale applied to estimated fold equity
                            (tight players fold more → > 1.0; fish call → < 1.0)
        bluff_freq_mult   — scale applied to hero's bluff probability
        call_freq_mult    — scale on how often opponent calls (informational)
        range_tightness   — how much tighter (+) or looser (–) opponent's range is
    """
    return PLAYER_PROFILES.get(player_profile, PLAYER_PROFILES["default"]).copy()
