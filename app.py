"""
Texas Hold'em Poker Decision Engine
====================================

INSTALLATION:
    pip install -r requirements.txt

RUN:
    python app.py
"""
from __future__ import annotations

import os
import secrets

# Load .env file if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from flask import Flask, render_template

from routes import api_bp
from routes.analyze_image import analyze_image_bp
from extensions import csrf, limiter
from utils.logging_setup import setup_logging, get_logger


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def create_app() -> Flask:
    setup_logging()
    logger = get_logger()

    app = Flask(__name__, template_folder="templates")

    # ── Core config ──────────────────────────────────────────────────────
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

    # ── Flask-WTF CSRF ───────────────────────────────────────────────────
    app.config["WTF_CSRF_TIME_LIMIT"] = None

    # ── Extensions ──────────────────────────────────────────────────────
    csrf.init_app(app)
    limiter.init_app(app)

    # ── Blueprints ───────────────────────────────────────────────────────
    app.register_blueprint(api_bp)
    app.register_blueprint(analyze_image_bp)

    # ── Root route — serve the engine UI directly ────────────────────────
    @app.route("/")
    def index():
        return render_template("app.html", user_plan="pro", feature_tier="pro", user_credits=0)

    # ── Error handlers ───────────────────────────────────────────────────
    @app.errorhandler(404)
    def not_found(e):
        return render_template("404.html"), 404

    @app.errorhandler(500)
    def server_error(e):
        return render_template("500.html"), 500

    logger.info("Poker Decision Engine initialized.")
    return app


if __name__ == "__main__":
    application = create_app()
    get_logger().info("Starting Poker Decision Engine on http://127.0.0.1:5000")
    application.run(host="127.0.0.1", port=5000, debug=True)

