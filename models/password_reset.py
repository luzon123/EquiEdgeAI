"""
Password reset tokens — one-use, time-limited tokens for the /forgot-password flow.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from models import db


class PasswordResetToken(db.Model):
    __tablename__ = "password_reset_tokens"

    id         = db.Column(db.Integer,     primary_key=True)
    user_id    = db.Column(db.Integer,     db.ForeignKey("users.id"), nullable=False, index=True)
    token      = db.Column(db.String(86),  unique=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime,    nullable=False, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime,    nullable=False)
    used       = db.Column(db.Boolean,     nullable=False, default=False)

    user = db.relationship("User", backref="reset_tokens")

    @staticmethod
    def generate(user_id: int, expires_minutes: int = 30) -> "PasswordResetToken":
        rt = PasswordResetToken(
            user_id    = user_id,
            token      = secrets.token_urlsafe(64),
            expires_at = datetime.utcnow() + timedelta(minutes=expires_minutes),
        )
        db.session.add(rt)
        db.session.commit()
        return rt

    def is_valid(self) -> bool:
        return not self.used and self.expires_at > datetime.utcnow()

    def invalidate(self) -> None:
        self.used = True
        db.session.commit()
