"""
Access control service.

Responsibilities:
  - Define per-tier feature flags (PLAN_FEATURES)
  - Check whether a user is allowed to make a decision
  - Deduct credits or record tier-based usage
  - Strip pro-only response fields for non-pro users

Tiers are granted as one-time purchases.
Stripe or any payment provider can be integrated by setting plan/plan_active
on the User model after a confirmed payment.
"""
from __future__ import annotations
from datetime import datetime
from typing import Any

from models import db

# ---------------------------------------------------------------------------
# Feature flags per access tier
# ---------------------------------------------------------------------------
PLAN_FEATURES: dict[str, Any] = {
    "pro": {
        "full_mode":       True,
        "what_if":         True,
        "ai_coaching":     True,   # reasoning, tags, ux_signals
        "hero_line":       True,
        "player_profile":  True,
        "max_simulations": 10_000,
    },
    "beginner": {
        "full_mode":       False,
        "what_if":         False,
        "ai_coaching":     False,  # no reasoning, no tags, no ux_signals
        "hero_line":       False,  # hero line locked
        "player_profile":  False,
        "max_simulations": 500,
    },
}


def get_feature_config(feature_tier: str) -> dict:
    """Return the feature flags dict for a given tier ('pro' or 'beginner')."""
    return PLAN_FEATURES.get(feature_tier, PLAN_FEATURES["beginner"])


# ---------------------------------------------------------------------------
# Access check
# ---------------------------------------------------------------------------
def check_access(user) -> tuple[bool, str]:
    """
    Determine if a user may make a decision.

    Returns:
        (True,  'plan')        – active plan, no credit deduction needed
        (True,  'credits')     – no plan but has credits (will be deducted)
        (False, 'deactivated') – account is deactivated
        (False, 'no_access')   – no plan and no credits
    """
    if not user.is_active:
        return False, "deactivated"
    if user.has_active_plan():
        return True, "plan"
    if user.credits > 0:
        return True, "credits"
    return False, "no_access"


# ---------------------------------------------------------------------------
# Usage recording
# ---------------------------------------------------------------------------
def deduct_credit(user) -> None:
    """Deduct one credit from a credits-based user and record usage."""
    user.credits         -= 1
    user.total_decisions += 1
    user.last_used_at     = datetime.utcnow()
    db.session.commit()


def record_decision(user) -> None:
    """Record usage for a plan user (no credit deduction)."""
    user.total_decisions += 1
    user.last_used_at     = datetime.utcnow()
    db.session.commit()


# ---------------------------------------------------------------------------
# Response gating
# ---------------------------------------------------------------------------
def apply_fast_mode_gating(response: dict) -> dict:
    """
    Strip coaching and analysis fields that are irrelevant in fast mode.
    Applied regardless of plan tier — fast mode is intentionally minimal.
    Mutates and returns the dict.
    """
    response["what_if"]        = {}
    response["reasoning"]      = []
    response["decision_tags"]  = []
    response["ux_signals"]     = {}
    response["ev_breakdown"]   = {}
    response.pop("population_adjustment", None)
    return response


def apply_plan_gating(response: dict, feature_tier: str) -> dict:
    """
    Strip pro-only fields from the API response for non-pro users.
    Mutates and returns the dict.
    """
    if feature_tier == "pro":
        return response

    # --- What-if analysis ---
    response["what_if"] = {}

    # --- AI coaching layer (reasoning, tags, ux_signals) ---
    # Beginner plan has no AI suggestions at all
    response["reasoning"]      = []
    response["decision_tags"]  = []
    response["ux_signals"]     = {}

    # --- Advanced exploit fields ---
    response.pop("population_adjustment", None)

    return response
