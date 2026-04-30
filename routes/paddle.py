"""
Paddle webhook route — card payment fulfillment.

Routes:
    POST /paddle/webhook    receive Paddle event webhooks (no session auth)

Access is ONLY granted here, after Paddle's HMAC-SHA256 signature is
verified against PADDLE_WEBHOOK_SECRET.  The frontend never grants access.

Fulfillment is idempotent: re-delivered webhooks for the same Paddle
transaction ID are safely ignored.  A database-level UNIQUE constraint on
paypal_order_id (reused for Paddle tx IDs) is the last line of defence
against a duplicate race; the IntegrityError is caught and treated as a
no-op so Paddle always receives 200 and stops retrying.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime
from decimal import Decimal, InvalidOperation

from flask import Blueprint, jsonify, request
from sqlalchemy.exc import IntegrityError

from extensions import csrf
from models import db
from models.purchase import Purchase
from models.user import User
from services.paypal import PLAN_PRICES
from utils.logging_setup import get_logger

paddle_bp = Blueprint("paddle", __name__, url_prefix="/paddle")
logger = get_logger()

_PLAN_RANK: dict[str, int] = {"none": 0, "beginner": 1, "pro": 2}

# Explicit allowlist — only tier upgrades are valid via Paddle.
# Credits are a separate product type handled elsewhere; do not mix them here.
_PADDLE_ALLOWED_PLANS: frozenset[str] = frozenset({"beginner", "pro"})


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------
def _verify_signature(headers, raw_body: bytes) -> bool:
    """
    Verify a Paddle webhook using HMAC-SHA256.

    Paddle sends: Paddle-Signature: ts=<unix_ts>;h1=<hex_digest>
    The signed payload is the string "<ts>:<raw_body_utf8>".

    Returns False (never raises) so callers can safely branch on the result.
    """
    secret = os.environ.get("PADDLE_WEBHOOK_SECRET", "").strip()
    if not secret:
        logger.warning("PADDLE_WEBHOOK_SECRET not configured — cannot verify Paddle webhook.")
        return False

    sig_header = headers.get("Paddle-Signature", "")
    parts: dict[str, str] = {}
    for segment in sig_header.split(";"):
        if "=" in segment:
            k, v = segment.split("=", 1)
            parts[k.strip()] = v.strip()

    ts = parts.get("ts", "")
    h1 = parts.get("h1", "")
    if not ts or not h1:
        logger.warning("Paddle-Signature header is missing ts or h1 fields.")
        return False

    try:
        body_str = raw_body.decode("utf-8")
    except UnicodeDecodeError:
        logger.warning("Paddle webhook body is not valid UTF-8 — rejecting.")
        return False

    signed_payload = f"{ts}:{body_str}"
    expected = hmac.new(
        secret.encode("utf-8"),
        signed_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, h1)


# ---------------------------------------------------------------------------
# Fulfillment
# ---------------------------------------------------------------------------
def _fulfill_paddle_transaction(
    *,
    user: User,
    plan: str,
    paddle_tx_id: str,
    amount_usd: str,
    currency: str,
) -> bool:
    """
    Grant the purchased tier and record the transaction.

    Returns True  if fulfilled now.
    Returns False if already processed (idempotent guard) or on a duplicate
    insert race (IntegrityError from the UNIQUE constraint).

    paddle_tx_id is stored in paypal_order_id — the column is a generic
    unique transaction identifier; source='paddle_webhook' distinguishes
    these records from PayPal ones.
    """
    existing = Purchase.query.filter_by(paypal_order_id=paddle_tx_id).first()
    if existing and existing.status == "completed":
        logger.info("Paddle fulfillment skipped — already processed | tx=%s", paddle_tx_id)
        return False

    now = datetime.utcnow()

    if existing:
        existing.status       = "completed"
        existing.source       = "paddle_webhook"
        existing.completed_at = now
    else:
        try:
            amount = Decimal(amount_usd)
        except InvalidOperation:
            amount = Decimal("0.00")

        purchase = Purchase(
            user_id           = user.id,
            plan              = plan,
            amount_usd        = amount,
            currency          = currency,
            paypal_order_id   = paddle_tx_id,   # generic tx-ID column; source distinguishes provider
            paypal_capture_id = None,
            status            = "completed",
            source            = "paddle_webhook",
            completed_at      = now,
        )
        db.session.add(purchase)

    # Tier upgrade only — never downgrade; credits are not handled here.
    if _PLAN_RANK.get(plan, 0) > _PLAN_RANK.get(user.plan, 0):
        user.plan         = plan
        user.plan_active  = True
        user.purchased_at = now
        user.updated_at   = now

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        logger.info(
            "Paddle fulfillment: concurrent duplicate delivery — already committed | tx=%s",
            paddle_tx_id,
        )
        return False

    logger.info(
        "Paddle purchase fulfilled | user=%s plan=%s tx=%s",
        user.id, plan, paddle_tx_id,
    )
    return True


# ---------------------------------------------------------------------------
# Webhook endpoint
# ---------------------------------------------------------------------------
@paddle_bp.route("/webhook", methods=["POST"])
@csrf.exempt
def webhook():
    raw_body = request.get_data()

    if not _verify_signature(request.headers, raw_body):
        logger.warning("Paddle webhook rejected — invalid signature.")
        return jsonify({"error": "Signature verification failed."}), 401

    try:
        event = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        logger.error("Paddle webhook body parse error: %s", exc)
        return jsonify({"error": "Invalid JSON."}), 400

    event_type = event.get("event_type", "")
    logger.info("Paddle webhook received | event_type=%s", event_type)

    if event_type == "transaction.completed":
        try:
            _handle_transaction_completed(event)
        except Exception as exc:
            # Return 200 so Paddle stops retrying; investigate via logs.
            logger.exception(
                "Unhandled error in Paddle transaction handler | event_type=%s error=%s",
                event_type, exc,
            )

    # Paddle expects 200 quickly for every event, including ones we don't act on.
    return jsonify({"status": "ok"}), 200


def _handle_transaction_completed(event: dict) -> None:
    data   = event.get("data", {})
    tx_id  = data.get("id", "")
    status = data.get("status", "")

    if status != "completed":
        logger.info("Paddle transaction not completed (status=%s) — ignoring | tx=%s", status, tx_id)
        return

    custom  = data.get("custom_data") or {}
    raw_uid = custom.get("user_id", "")
    plan    = custom.get("plan", "").strip().lower()

    if not raw_uid or not plan:
        logger.error(
            "Paddle webhook missing custom_data.user_id or custom_data.plan | tx=%s", tx_id
        )
        return

    try:
        user_id = int(raw_uid)
    except (ValueError, TypeError):
        logger.error("Paddle webhook invalid user_id=%r | tx=%s", raw_uid, tx_id)
        return

    if plan not in _PADDLE_ALLOWED_PLANS:
        logger.error("Paddle webhook unknown or disallowed plan=%r | tx=%s", plan, tx_id)
        return

    user = User.query.get(user_id)
    if not user:
        logger.error("Paddle webhook user not found | user_id=%s tx=%s", user_id, tx_id)
        return

    # Amount is in lowest denomination (cents for USD); divide by 100.
    currency  = data.get("currency_code", "USD")
    totals    = (data.get("details") or {}).get("totals") or {}
    raw_total = totals.get("total", "0")
    try:
        amount_usd = f"{int(raw_total) / 100:.2f}"
    except (ValueError, TypeError):
        amount_usd = "0.00"

    _fulfill_paddle_transaction(
        user         = user,
        plan         = plan,
        paddle_tx_id = tx_id,
        amount_usd   = amount_usd,
        currency     = currency,
    )
