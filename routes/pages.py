"""
Page routes — all non-API HTML pages.
"""
from __future__ import annotations

from flask import Blueprint, render_template, redirect, url_for
from flask_login import login_required, current_user

pages_bp = Blueprint("pages", __name__)


# ---------------------------------------------------------------------------
# Main app (the poker engine)
# ---------------------------------------------------------------------------
@pages_bp.route("/app")
@login_required
def app_page():
    plan_tier    = current_user.get_plan_tier()
    feature_tier = current_user.get_feature_tier()
    return render_template(
        "app.html",
        user_plan=plan_tier,
        feature_tier=feature_tier,
        user_credits=current_user.credits,
    )


# ---------------------------------------------------------------------------
# Landing / home
# ---------------------------------------------------------------------------
@pages_bp.route("/")
def home():
    if current_user.is_authenticated:
        return render_template("home.html", logged_in=True)
    return render_template("home.html", logged_in=False)


# ---------------------------------------------------------------------------
# Marketing / product pages
# ---------------------------------------------------------------------------
@pages_bp.route("/pricing")
def pricing():
    return render_template("pricing.html")


@pages_bp.route("/contact")
def contact():
    return render_template("contact.html")


@pages_bp.route("/terms")
def terms():
    return render_template("terms.html")


@pages_bp.route("/privacy")
def privacy():
    return render_template("privacy.html")


@pages_bp.route("/legal")
def legal():
    return render_template("legal.html")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
@pages_bp.route("/settings")
@login_required
def settings():
    return render_template("settings.html")
