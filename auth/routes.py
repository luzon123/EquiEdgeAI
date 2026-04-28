"""
Authentication routes: /login  /register  /logout  /forgot-password  /reset-password/<token>
"""
from __future__ import annotations
import re
from urllib.parse import urlparse, urljoin

from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_user, logout_user, login_required, current_user

from extensions import limiter
from models import db
from models.user import User
from models.password_reset import PasswordResetToken
from utils.logging_setup import get_logger

auth_bp = Blueprint("auth", __name__)
logger  = get_logger()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_USER_RE  = re.compile(r"^[A-Za-z0-9_]+$")


def _valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email))


def _is_safe_redirect_url(target: str) -> bool:
    """Return True only if target stays on the same host (prevents open-redirect)."""
    ref  = urlparse(request.host_url)
    test = urlparse(urljoin(request.host_url, target))
    return test.scheme in ("http", "https") and ref.netloc == test.netloc


def _send_reset_email(user: User, token: str) -> None:
    """Send a password-reset email. Silently fails if mail is not configured."""
    from flask import current_app
    from flask_mail import Message
    from extensions import mail

    mail_server = current_app.config.get("MAIL_SERVER", "")
    if not mail_server:
        logger.warning("MAIL_SERVER not configured — reset email not sent for user %s", user.id)
        return

    try:
        reset_url = url_for("auth.reset_password", token=token, _external=True)
        msg = Message(
            subject    = "Reset your EquiEdge AI password",
            recipients = [user.email],
            body       = (
                f"Hi {user.username},\n\n"
                f"Click the link below to reset your password (valid for 30 minutes):\n"
                f"{reset_url}\n\n"
                f"If you did not request this, you can safely ignore this email.\n\n"
                f"EquiEdge AI"
            ),
        )
        mail.send(msg)
        logger.info("Password reset email sent to user %s", user.id)
    except Exception as exc:
        logger.warning("Could not send reset email to user %s: %s", user.id, exc)


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("15 per minute")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("pages.app_page"))

    if request.method == "POST":
        email    = request.form.get("email",    "").strip().lower()
        password = request.form.get("password", "")
        remember = bool(request.form.get("remember"))

        user = User.query.filter_by(email=email).first()

        if not user or not user.check_password(password):
            flash("Invalid email or password.", "error")
            return render_template("auth/login.html")

        if not user.is_active:
            flash("Your account has been deactivated. Contact support.", "error")
            return render_template("auth/login.html")

        login_user(user, remember=remember)
        # Validate the next param to prevent open-redirect attacks
        next_page = request.args.get("next", "")
        if not next_page or not _is_safe_redirect_url(next_page):
            next_page = url_for("pages.app_page")
        return redirect(next_page)

    return render_template("auth/login.html")


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------
@auth_bp.route("/register", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def register():
    if current_user.is_authenticated:
        return redirect(url_for("pages.app_page"))

    form_data = {}

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email    = request.form.get("email",    "").strip().lower()
        password = request.form.get("password", "")
        confirm  = request.form.get("confirm",  "")
        form_data = {"username": username, "email": email}

        errors = []

        if not username or len(username) < 3 or len(username) > 32:
            errors.append("Username must be 3–32 characters.")
        elif not _USER_RE.match(username):
            errors.append("Username can only contain letters, numbers, and underscores.")

        if not _valid_email(email):
            errors.append("Enter a valid email address.")

        if len(password) < 8:
            errors.append("Password must be at least 8 characters.")
        elif password != confirm:
            errors.append("Passwords do not match.")

        if not errors:
            if User.query.filter_by(username=username).first():
                errors.append("Username is already taken.")
            if User.query.filter_by(email=email).first():
                errors.append("An account with that email already exists.")

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("auth/register.html", **form_data)

        user = User(username=username, email=email, credits=5)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        login_user(user)
        flash(
            "Welcome to EquiEdge AI! You have 5 free credits to try the engine.",
            "success",
        )
        return redirect(url_for("pages.app_page"))

    return render_template("auth/register.html", **form_data)


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------
@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("pages.home"))


# ---------------------------------------------------------------------------
# Forgot password
# ---------------------------------------------------------------------------
@auth_bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("pages.app_page"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user  = User.query.filter_by(email=email).first()

        # Always show the same message — don't reveal whether email is registered
        if user and user.is_active:
            rt = PasswordResetToken.generate(user.id)
            _send_reset_email(user, rt.token)

        flash("If that email is registered, a reset link has been sent.", "info")
        return redirect(url_for("auth.login"))

    return render_template("auth/forgot_password.html")


# ---------------------------------------------------------------------------
# Reset password
# ---------------------------------------------------------------------------
@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def reset_password(token: str):
    rt = PasswordResetToken.query.filter_by(token=token).first()

    if not rt or not rt.is_valid():
        flash("This reset link is invalid or has expired.", "error")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm  = request.form.get("confirm",  "")

        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return render_template("auth/reset_password.html", token=token)

        if password != confirm:
            flash("Passwords do not match.", "error")
            return render_template("auth/reset_password.html", token=token)

        rt.user.set_password(password)
        rt.invalidate()
        flash("Password updated. Please log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", token=token)

