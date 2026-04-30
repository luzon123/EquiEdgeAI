"""
Texas Hold'em Poker Decision Engine — SaaS Edition
====================================================

INSTALLATION:
    pip install flask flask-sqlalchemy flask-login flask-wtf flask-limiter flask-mail treys werkzeug

FIRST RUN — create an admin account:
    flask create-admin

RUN SERVER:
    python app.py
"""
from __future__ import annotations

import os
import warnings
import click
from datetime import datetime

from flask import Flask, render_template
from flask_login import LoginManager

from models import db
from models.user import User
from routes import api_bp, pages_bp
from routes.checkout import checkout_bp
from routes.paddle import paddle_bp
from auth import auth_bp
from admin import admin_bp
import models.purchase          # noqa: F401 — registers Purchase with SQLAlchemy metadata
import models.password_reset    # noqa: F401 — registers PasswordResetToken
from extensions import csrf, limiter, mail
from utils.logging_setup import setup_logging, get_logger

_DEV_SECRET = "dev-secret-key-CHANGE-IN-PRODUCTION"


# ---------------------------------------------------------------------------
# Startup validation
# ---------------------------------------------------------------------------
def _validate_env(secret_key: str) -> None:
    """
    Fail fast if critical environment variables are missing or unsafe.
    Called once inside create_app() before any network/DB work starts.
    """
    logger = get_logger()

    # SECRET_KEY must not be the dev placeholder in any context
    if secret_key == _DEV_SECRET:
        # Warning already emitted by create_app(); allow local dev to continue.
        logger.warning(
            "SECURITY: Running with the default dev SECRET_KEY. "
            "Set SECRET_KEY before accepting real traffic."
        )

    # PAYPAL_MODE is always required — no silent default in production
    paypal_mode = os.environ.get("PAYPAL_MODE", "").strip().lower()
    if not paypal_mode:
        raise RuntimeError(
            "PAYPAL_MODE environment variable is not set. "
            "Set it to 'sandbox' for testing or 'live' for production."
        )
    if paypal_mode not in ("sandbox", "live"):
        raise RuntimeError(
            f"PAYPAL_MODE must be 'sandbox' or 'live', got {paypal_mode!r}."
        )

    # PayPal credentials are required regardless of mode
    if not os.environ.get("PAYPAL_CLIENT_ID"):
        raise RuntimeError("PAYPAL_CLIENT_ID environment variable is not set.")
    if not os.environ.get("PAYPAL_CLIENT_SECRET"):
        raise RuntimeError("PAYPAL_CLIENT_SECRET environment variable is not set.")

    # Webhook ID is mandatory in live mode (signature verification cannot be skipped)
    if paypal_mode == "live" and not os.environ.get("PAYPAL_WEBHOOK_ID"):
        raise RuntimeError(
            "PAYPAL_WEBHOOK_ID is required when PAYPAL_MODE=live. "
            "Obtain the webhook ID from the PayPal developer dashboard."
        )

    # Warn (not raise) if mail is unconfigured — email features degrade gracefully
    if not os.environ.get("MAIL_SERVER"):
        logger.warning(
            "MAIL_SERVER is not set — password reset and contact emails will not be sent."
        )
    if not os.environ.get("CONTACT_RECIPIENT"):
        logger.warning(
            "CONTACT_RECIPIENT is not set — contact form submissions will be dropped."
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def create_app() -> Flask:
    setup_logging()
    logger = get_logger()

    app = Flask(__name__, template_folder="templates")

    # ── Core config ──────────────────────────────────────────────────────
    secret_key = os.environ.get("SECRET_KEY", _DEV_SECRET)
    app.config["SECRET_KEY"] = secret_key
    if secret_key == _DEV_SECRET:
        warnings.warn(
            "Using the default dev SECRET_KEY — set the SECRET_KEY environment "
            "variable before deploying to production.",
            stacklevel=2,
        )

    # Resolve database URL.
    # Render PostgreSQL URLs historically use postgres:// but SQLAlchemy requires postgresql://.
    _db_url = os.environ.get("DATABASE_URL", "").strip()
    if _db_url.startswith("postgres://"):
        _db_url = "postgresql://" + _db_url[len("postgres://"):]

    if not _db_url:
        # On Render (ephemeral filesystem) SQLite means data loss on every redeploy.
        if os.environ.get("RENDER"):
            raise RuntimeError(
                "DATABASE_URL is not set. "
                "Create a Render PostgreSQL database and link it to this service "
                "via the DATABASE_URL environment variable, then redeploy."
            )
        # Local development only — SQLite is acceptable here.
        _db_url = "sqlite:///" + os.path.join(
            os.path.abspath(os.path.dirname(__file__)), "poker.db"
        )

    app.config["SQLALCHEMY_DATABASE_URI"] = _db_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # ── Flask-Mail ───────────────────────────────────────────────────────
    app.config["MAIL_SERVER"]         = os.environ.get("MAIL_SERVER", "")
    app.config["MAIL_PORT"]           = int(os.environ.get("MAIL_PORT", "587"))
    app.config["MAIL_USE_TLS"]        = os.environ.get("MAIL_USE_TLS", "true").lower() == "true"
    app.config["MAIL_USERNAME"]       = os.environ.get("MAIL_USERNAME", "")
    app.config["MAIL_PASSWORD"]       = os.environ.get("MAIL_PASSWORD", "")
    app.config["MAIL_DEFAULT_SENDER"] = os.environ.get("MAIL_DEFAULT_SENDER", "noreply@equiedgeai.io")

    # ── Flask-WTF CSRF ───────────────────────────────────────────────────
    # 7200 seconds (2 hours) — long enough for a normal session, not forever.
    app.config["WTF_CSRF_TIME_LIMIT"] = 7200

    # ── Production env-var validation ───────────────────────────────────
    _validate_env(secret_key)

    # ── Extensions ──────────────────────────────────────────────────────
    db.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    mail.init_app(app)

    login_manager = LoginManager(app)
    login_manager.login_view    = "auth.login"          # type: ignore[assignment]
    login_manager.login_message = "Please log in to access the engine."
    login_manager.login_message_category = "info"

    @login_manager.user_loader
    def load_user(user_id: str):
        return User.query.get(int(user_id))

    # ── Blueprints ───────────────────────────────────────────────────────
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(pages_bp)
    app.register_blueprint(checkout_bp)
    app.register_blueprint(paddle_bp)

    # ── DB init ──────────────────────────────────────────────────────────
    with app.app_context():
        db.create_all()

    # ── Error handlers ───────────────────────────────────────────────────
    @app.errorhandler(404)
    def not_found(e):
        return render_template("404.html"), 404

    @app.errorhandler(500)
    def server_error(e):
        return render_template("500.html"), 500

    # ── CLI commands ─────────────────────────────────────────────────────
    @app.cli.command("create-admin")
    @click.option("--username", prompt=True,                    help="Admin username")
    @click.option("--email",    prompt=True,                    help="Admin email")
    @click.option("--password", prompt=True, hide_input=True,
                  confirmation_prompt=True,                     help="Admin password")
    def create_admin(username: str, email: str, password: str) -> None:
        """Create an admin user interactively."""
        with app.app_context():
            if User.query.filter_by(username=username).first():
                click.echo(f"Error: username '{username}' already exists.")
                return
            if User.query.filter_by(email=email).first():
                click.echo(f"Error: email '{email}' already registered.")
                return
            user = User(
                username=username,
                email=email,
                is_admin=True,
                plan="pro",
                plan_active=True,
                purchased_at=datetime.utcnow(),
                credits=9999,
            )
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            click.echo(f"Admin user '{username}' created with Pro tier.")

    logger.info("Poker Decision Engine (SaaS) initialized.")
    return app


if __name__ == "__main__":
    application = create_app()
    get_logger().info("Starting Poker Decision Engine on http://0.0.0.0:5000")
    # Debug mode is opt-in via env var — never hardcoded
    _debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    application.run(host="0.0.0.0", port=5000, debug=_debug)

