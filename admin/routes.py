"""
Admin panel — only accessible to users with is_admin=True.
Routes: /admin/  /admin/users  /admin/user/<id>
"""
from __future__ import annotations
from functools import wraps
from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user

from models import db
from models.user import User
from utils.logging_setup import get_logger

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")
logger   = get_logger()


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------
def admin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@admin_bp.route("/")
@admin_required
def dashboard():
    total_users     = User.query.count()
    active_users    = User.query.filter_by(is_active=True).count()
    pro_users       = User.query.filter_by(plan="pro",      plan_active=True).count()
    beginner_users  = User.query.filter_by(plan="beginner", plan_active=True).count()
    # plan_active=True means a one-time purchase was granted
    total_decisions = db.session.query(
        db.func.sum(User.total_decisions)
    ).scalar() or 0
    recent_users    = (
        User.query.order_by(User.created_at.desc()).limit(5).all()
    )

    return render_template(
        "admin/dashboard.html",
        total_users=total_users,
        active_users=active_users,
        pro_users=pro_users,
        beginner_users=beginner_users,
        total_decisions=total_decisions,
        recent_users=recent_users,
    )


# ---------------------------------------------------------------------------
# Users list
# ---------------------------------------------------------------------------
@admin_bp.route("/users")
@admin_required
def users():
    search = request.args.get("q", "").strip()
    query  = User.query
    if search:
        like  = f"%{search}%"
        query = query.filter(
            User.username.ilike(like) | User.email.ilike(like)
        )
    all_users = query.order_by(User.created_at.desc()).all()
    return render_template("admin/users.html", users=all_users, search=search)


# ---------------------------------------------------------------------------
# User detail + edit
# ---------------------------------------------------------------------------
@admin_bp.route("/user/<int:user_id>", methods=["GET", "POST"])
@admin_required
def user_detail(user_id):
    user = User.query.get_or_404(user_id)

    if request.method == "POST":
        action = request.form.get("action", "")

        # ── Grant purchase tier ──────────────────────────────────
        if action == "update_plan":
            new_plan = request.form.get("plan", "none")
            if new_plan in ("none", "beginner", "pro"):
                old_plan      = user.plan
                user.plan         = new_plan
                user.plan_active  = new_plan != "none"
                user.purchased_at = datetime.utcnow() if new_plan != "none" else None
                user.updated_at   = datetime.utcnow()
                db.session.commit()
                logger.info(
                    "ADMIN ACTION | admin=%s(%d) updated plan for user=%s(%d): %s -> %s",
                    current_user.username, current_user.id,
                    user.username, user.id, old_plan, new_plan,
                )
                if new_plan == "none":
                    flash("Purchase tier removed.", "success")
                else:
                    flash(f"'{new_plan.capitalize()}' tier granted.", "success")
            else:
                flash("Invalid tier.", "error")

        # ── Adjust credits ───────────────────────────────────────
        elif action == "update_credits":
            try:
                delta        = int(request.form.get("credits_delta", 0))
                old_credits  = user.credits
                user.credits = max(0, user.credits + delta)
                user.updated_at = datetime.utcnow()
                db.session.commit()
                logger.info(
                    "ADMIN ACTION | admin=%s(%d) adjusted credits for user=%s(%d): %d -> %d (delta=%+d)",
                    current_user.username, current_user.id,
                    user.username, user.id, old_credits, user.credits, delta,
                )
                flash(f"Credits set to {user.credits}.", "success")
            except (ValueError, TypeError):
                flash("Invalid credit amount.", "error")

        # ── Toggle active ────────────────────────────────────────
        elif action == "toggle_active":
            user.is_active  = not user.is_active
            user.updated_at = datetime.utcnow()
            db.session.commit()
            state = "activated" if user.is_active else "deactivated"
            logger.info(
                "ADMIN ACTION | admin=%s(%d) %s user=%s(%d)",
                current_user.username, current_user.id, state, user.username, user.id,
            )
            flash(f"User account {state}.", "success")

        # ── Toggle admin ─────────────────────────────────────────
        elif action == "toggle_admin":
            if user.id == current_user.id:
                flash("You cannot change your own admin status.", "error")
            else:
                user.is_admin   = not user.is_admin
                user.updated_at = datetime.utcnow()
                db.session.commit()
                state = "granted" if user.is_admin else "revoked"
                logger.info(
                    "ADMIN ACTION | admin=%s(%d) %s admin rights for user=%s(%d)",
                    current_user.username, current_user.id, state, user.username, user.id,
                )
                flash(f"Admin access {state}.", "success")

        return redirect(url_for("admin.user_detail", user_id=user_id))

    return render_template("admin/user_detail.html", user=user)
