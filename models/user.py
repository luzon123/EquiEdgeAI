from __future__ import annotations
from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from models import db


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id              = db.Column(db.Integer, primary_key=True)
    username        = db.Column(db.String(64),  unique=True, nullable=False, index=True)
    email           = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash   = db.Column(db.String(256), nullable=False)

    # Purchase tier: 'none' | 'beginner' | 'pro'
    plan            = db.Column(db.String(20),  nullable=False, default="none")
    plan_active     = db.Column(db.Boolean,     nullable=False, default=False)
    plan_expires_at = db.Column(db.DateTime,    nullable=True)   # legacy — not used for access
    purchased_at    = db.Column(db.DateTime,    nullable=True)   # when the tier was granted

    # Credits (purchased separately — no free credits on sign-up)
    credits         = db.Column(db.Integer,     nullable=False, default=0)

    # Usage tracking
    total_decisions = db.Column(db.Integer,     nullable=False, default=0)
    last_used_at    = db.Column(db.DateTime,    nullable=True)

    # Account status
    is_active       = db.Column(db.Boolean,     nullable=False, default=True)
    is_admin        = db.Column(db.Boolean,     nullable=False, default=False)

    # Timestamps
    created_at      = db.Column(db.DateTime,    nullable=False, default=datetime.utcnow)
    updated_at      = db.Column(db.DateTime,    nullable=False, default=datetime.utcnow,
                                onupdate=datetime.utcnow)

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------
    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    # ------------------------------------------------------------------
    # Plan / access helpers
    # ------------------------------------------------------------------
    def has_active_plan(self) -> bool:
        """True if the user has a purchased tier (no expiry check)."""
        return self.plan_active and self.plan != "none"

    def has_access(self) -> bool:
        """True if the user can make decisions (plan or credits)."""
        return self.has_active_plan() or self.credits > 0

    def get_plan_tier(self) -> str:
        """Returns: 'pro' | 'beginner' | 'credits' | 'none'."""
        if self.has_active_plan():
            return self.plan  # 'pro' or 'beginner'
        if self.credits > 0:
            return "credits"
        return "none"

    def get_feature_tier(self) -> str:
        """Returns the feature access tier: 'pro' or 'beginner'.
        Credits and beginner plan both get 'beginner' features."""
        if self.has_active_plan() and self.plan == "pro":
            return "pro"
        return "beginner"

    def __repr__(self) -> str:
        return f"<User {self.username!r} plan={self.plan!r} credits={self.credits}>"
