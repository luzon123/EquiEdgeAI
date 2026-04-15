"""
Purchase model — records every one-time PayPal checkout attempt.

paypal_order_id has a UNIQUE constraint so duplicate webhook deliveries and
double-clicks on the capture endpoint are safe: the second call hits the
unique constraint and can be handled idempotently.

status lifecycle:
  'pending'   — order created server-side, awaiting PayPal approval + capture
  'completed' — payment captured and access granted
  'failed'    — capture was attempted but PayPal returned a non-COMPLETED status
"""
from __future__ import annotations
from datetime import datetime

from models import db


class Purchase(db.Model):
    __tablename__ = "purchases"

    id                = db.Column(db.Integer,        primary_key=True)
    user_id           = db.Column(db.Integer,        db.ForeignKey("users.id"),
                                                     nullable=False, index=True)
    plan              = db.Column(db.String(20),     nullable=False)
    # plan: 'beginner' | 'pro'

    amount_usd        = db.Column(db.Numeric(10, 2), nullable=False)
    currency          = db.Column(db.String(8),      nullable=False, default="USD")

    # PayPal identifiers
    paypal_order_id   = db.Column(db.String(64),     nullable=False,
                                                     unique=True, index=True)
    paypal_capture_id = db.Column(db.String(64),     nullable=True)

    # Processing metadata
    status            = db.Column(db.String(20),     nullable=False, default="pending")
    source            = db.Column(db.String(20),     nullable=False, default="checkout")
    # source: 'checkout' (browser-captured) | 'webhook' (PayPal-pushed backup)

    created_at    = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    completed_at  = db.Column(db.DateTime, nullable=True)

    user = db.relationship("User", backref=db.backref("purchases", lazy="dynamic"))

    def __repr__(self) -> str:
        return (
            f"<Purchase #{self.id} user={self.user_id} plan={self.plan!r} "
            f"status={self.status!r} order={self.paypal_order_id!r}>"
        )
