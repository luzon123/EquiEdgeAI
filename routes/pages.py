"""
Page routes — all non-API HTML pages.
"""
from __future__ import annotations
import os

from flask import Blueprint, render_template, redirect, url_for, request, flash, send_from_directory
from flask_login import login_required, current_user

from utils.logging_setup import get_logger

pages_bp = Blueprint("pages", __name__)
logger   = get_logger()


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


@pages_bp.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        subject = request.form.get("subject", "").strip()
        message = request.form.get("message", "").strip()

        if not subject or not message:
            flash("Please fill in both fields.", "error")
            return redirect(url_for("pages.contact"))

        reply_to = ""
        if current_user.is_authenticated:
            reply_to = current_user.email
        else:
            reply_to = request.form.get("reply_email", "").strip()

        _send_contact_email(subject=subject, message=message, reply_to=reply_to)
        flash("Message sent! We'll get back to you within 24–48 hours.", "success")
        return redirect(url_for("pages.contact"))

    return render_template("contact.html")


def _send_contact_email(subject: str, message: str, reply_to: str) -> None:
    """Forward contact form submission to CONTACT_RECIPIENT. Silently fails if not configured."""
    from flask import current_app
    from flask_mail import Message
    from extensions import mail

    recipient = os.environ.get("CONTACT_RECIPIENT", "")
    if not recipient or not current_app.config.get("MAIL_SERVER", ""):
        logger.warning("Contact email not sent — MAIL_SERVER or CONTACT_RECIPIENT not configured.")
        return

    try:
        msg = Message(
            subject    = f"[EquiEdge AI Contact] {subject}",
            recipients = [recipient],
            body       = f"From: {reply_to or 'anonymous'}\n\n{message}",
            reply_to   = reply_to or None,
        )
        mail.send(msg)
        logger.info("Contact form email sent: %s", subject)
    except Exception as exc:
        logger.warning("Could not send contact email: %s", exc)


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


# ---------------------------------------------------------------------------
# Robots.txt
# ---------------------------------------------------------------------------
@pages_bp.route("/robots.txt")
def robots():
    static_dir = os.path.join(pages_bp.root_path, "..", "static")
    return send_from_directory(os.path.abspath(static_dir), "robots.txt")

