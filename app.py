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
from auth import auth_bp
from admin import admin_bp
import models.purchase          # noqa: F401 — registers Purchase with SQLAlchemy metadata
import models.password_reset    # noqa: F401 — registers PasswordResetToken
from extensions import csrf, limiter, mail
from utils.logging_setup import setup_logging, get_logger

_DEV_SECRET = "dev-secret-key-CHANGE-IN-PRODUCTION"


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

    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
        "DATABASE_URL",
        "sqlite:///" + os.path.join(os.path.abspath(os.path.dirname(__file__)), "poker.db"),
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # ── Flask-Mail ───────────────────────────────────────────────────────
    app.config["MAIL_SERVER"]         = os.environ.get("MAIL_SERVER", "")
    app.config["MAIL_PORT"]           = int(os.environ.get("MAIL_PORT", "587"))
    app.config["MAIL_USE_TLS"]        = os.environ.get("MAIL_USE_TLS", "true").lower() == "true"
    app.config["MAIL_USERNAME"]       = os.environ.get("MAIL_USERNAME", "")
    app.config["MAIL_PASSWORD"]       = os.environ.get("MAIL_PASSWORD", "")
    app.config["MAIL_DEFAULT_SENDER"] = os.environ.get("MAIL_DEFAULT_SENDER", "noreply@equiedgeai.io")

    # ── Flask-WTF CSRF ───────────────────────────────────────────────────
    app.config["WTF_CSRF_TIME_LIMIT"] = None   # token valid for entire session

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
    application.run(host="0.0.0.0", port=5000, debug=True)

