"""
Checkout blueprint — PayPal one-time purchase integration.

Routes:
    GET  /checkout/<plan>         checkout page (login required)
    POST /checkout/create-order   create a PayPal order         (JSON, login required)
    POST /checkout/capture-order  capture an approved order     (JSON, login required)
    POST /checkout/webhook        receive PayPal event webhooks (no auth)
    GET  /checkout/success        post-payment success page
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from decimal import Decimal
from functools import wraps

from flask import (
    Blueprint, abort, jsonify, redirect, render_template,
    request, url_for,
)
from flask_login import current_user, login_required

from extensions import csrf, limiter
from models import db
from models.user import User
from models.purchase import Purchase
from services.paypal import (
    PLAN_PRICES, PLAN_LABELS, CREDIT_PACKS,
    create_order as pp_create_order,
    capture_order as pp_capture_order,
    verify_webhook_signature,
    extract_order_info,
)
from utils.logging_setup import get_logger

checkout_bp = Blueprint("checkout", __name__, url_prefix="/checkout")
logger = get_logger()

# Tier rank used to decide whether an upgrade is warranted
_PLAN_RANK: dict[str, int] = {"none": 0, "beginner": 1, "pro": 2}


# ---------------------------------------------------------------------------
# Auth decorator (JSON 401 for API routes, redirect for page routes)
# ---------------------------------------------------------------------------
def _api_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({"error": "Authentication required."}), 401
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Idempotent fulfillment — called by both the capture endpoint and the webhook
# ---------------------------------------------------------------------------
def _fulfill_purchase(
    *,
    user: User,
    plan: str,
    paypal_order_id: str,
    paypal_capture_id: str,
    amount_usd: str,
    currency: str,
    source: str = "checkout",
) -> bool:
    """
    Grant the purchased tier to the user and record the transaction.

    Returns True  if this call actually completed the fulfillment.
    Returns False if the order was already processed (idempotent guard).

    This function is safe to call multiple times with the same paypal_order_id.
    On the second call the unique-constraint query finds the existing completed
    record and returns early without touching the User row again.
    """
    existing = Purchase.query.filter_by(paypal_order_id=paypal_order_id).first()

    if existing and existing.status == "completed":
        logger.info(
            "Fulfillment skipped — order already processed | order_id=%s", paypal_order_id
        )
        return False

    now = datetime.utcnow()

    if existing:
        # Update the pending record created when the order was first made
        existing.paypal_capture_id = paypal_capture_id
        existing.status            = "completed"
        existing.source            = source
        existing.completed_at      = now
    else:
        # Webhook arrived without a matching pending record (edge case: browser
        # crashed before create-order response was stored).  Create it now.
        purchase = Purchase(
            user_id           = user.id,
            plan              = plan,
            amount_usd        = Decimal(amount_usd),
            currency          = currency,
            paypal_order_id   = paypal_order_id,
            paypal_capture_id = paypal_capture_id,
            status            = "completed",
            source            = source,
            completed_at      = now,
        )
        db.session.add(purchase)

    # Credit pack: add credits to balance, don't change plan
    if plan in CREDIT_PACKS:
        user.credits    += CREDIT_PACKS[plan]
        user.updated_at  = now
    # Tier upgrade: only upgrade, never downgrade
    elif _PLAN_RANK.get(plan, 0) > _PLAN_RANK.get(user.plan, 0):
        user.plan         = plan
        user.plan_active  = True
        user.purchased_at = now
        user.updated_at   = now

    db.session.commit()
    logger.info(
        "Purchase fulfilled | user=%s plan=%s order_id=%s source=%s",
        user.id, plan, paypal_order_id, source,
    )
    return True


# ---------------------------------------------------------------------------
# GET /checkout/<plan>  — checkout page
# ---------------------------------------------------------------------------
@checkout_bp.route("/<plan>")
@login_required
def checkout_page(plan: str):
    if plan not in PLAN_PRICES:
        abort(404)

    # Credit packs can always be purchased; tier re-purchase is blocked
    if plan not in CREDIT_PACKS and current_user.has_active_plan():
        current_rank = _PLAN_RANK.get(current_user.plan, 0)
        new_rank     = _PLAN_RANK.get(plan, 0)
        if new_rank <= current_rank:
            return redirect(url_for("pages.app_page"))

    paypal_client_id = os.environ.get("PAYPAL_CLIENT_ID", "")
    if not paypal_client_id:
        logger.error("PAYPAL_CLIENT_ID not configured — checkout unavailable.")
        abort(500)

    return render_template(
        "checkout/checkout.html",
        plan            = plan,
        plan_label      = PLAN_LABELS[plan],
        plan_price      = PLAN_PRICES[plan],
        paypal_client_id= paypal_client_id,
        paypal_mode     = os.environ.get("PAYPAL_MODE", "sandbox"),
    )


# ---------------------------------------------------------------------------
# POST /checkout/create-order  — create a PayPal order (AJAX)
# ---------------------------------------------------------------------------
@checkout_bp.route("/create-order", methods=["POST"])
@_api_login_required
@limiter.limit("15 per minute")
def create_order():
    data = request.get_json(silent=True) or {}
    plan = data.get("plan", "")

    # Always validate plan server-side — never trust the client for price/plan
    if plan not in PLAN_PRICES:
        return jsonify({"error": "Invalid plan."}), 400

    # Block re-purchase of same or lower tier (credit packs are always allowed)
    if plan not in CREDIT_PACKS and current_user.has_active_plan():
        if _PLAN_RANK.get(plan, 0) <= _PLAN_RANK.get(current_user.plan, 0):
            return jsonify({"error": "You already have this tier or higher."}), 400

    try:
        order = pp_create_order(plan=plan, user_id=current_user.id)
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.exception("PayPal create_order failed: %s", exc)
        return jsonify({"error": "Could not reach payment provider. Please try again."}), 502

    order_id = order.get("id")
    if not order_id:
        return jsonify({"error": "Unexpected response from payment provider."}), 502

    # Persist a pending record so the webhook can find the user
    try:
        pending = Purchase(
            user_id         = current_user.id,
            plan            = plan,
            amount_usd      = Decimal(PLAN_PRICES[plan]),
            currency        = "USD",
            paypal_order_id = order_id,
            status          = "pending",
            source          = "checkout",
        )
        db.session.add(pending)
        db.session.commit()
    except Exception as exc:
        # Non-fatal: capture endpoint and webhook can still complete the flow
        logger.warning("Could not persist pending purchase record: %s", exc)
        db.session.rollback()

    return jsonify({"order_id": order_id})


# ---------------------------------------------------------------------------
# POST /checkout/capture-order  — capture approved order (AJAX)
# ---------------------------------------------------------------------------
@checkout_bp.route("/capture-order", methods=["POST"])
@_api_login_required
@limiter.limit("15 per minute")
def capture_order():
    data             = request.get_json(silent=True) or {}
    paypal_order_id  = data.get("order_id", "").strip()

    if not paypal_order_id:
        return jsonify({"error": "Missing order_id."}), 400

    # Confirm the pending record belongs to this user (prevents order-swapping)
    pending = Purchase.query.filter_by(paypal_order_id=paypal_order_id).first()
    if pending and pending.user_id != current_user.id:
        logger.warning(
            "Order ID mismatch | order_id=%s expected_user=%s actual_user=%s",
            paypal_order_id, pending.user_id, current_user.id,
        )
        return jsonify({"error": "Order not found."}), 404

    # Idempotent: already completed by webhook before the browser got here
    if pending and pending.status == "completed":
        return jsonify({"success": True})

    try:
        result = pp_capture_order(paypal_order_id)
    except Exception as exc:
        logger.exception("PayPal capture_order failed: %s", exc)
        return jsonify({"error": "Payment capture failed. Please contact support."}), 502

    if result.get("status") != "COMPLETED":
        logger.warning(
            "Capture status not COMPLETED | order_id=%s status=%s",
            paypal_order_id, result.get("status"),
        )
        return jsonify({
            "error": f"Payment not completed (status: {result.get('status', 'unknown')}). "
                     "Please try again or contact support."
        }), 402

    info = extract_order_info(result)

    # Re-validate plan from PayPal's response — never trust the stored plan alone;
    # the amount is the canonical proof of what was paid
    captured_plan = info["plan"] if info["plan"] in PLAN_PRICES else (
        pending.plan if pending else None
    )
    if not captured_plan:
        logger.error("Cannot determine plan from capture response | order_id=%s", paypal_order_id)
        return jsonify({"error": "Could not verify purchased plan. Contact support."}), 500

    _fulfill_purchase(
        user            = current_user,
        plan            = captured_plan,
        paypal_order_id = info["order_id"],
        paypal_capture_id = info["capture_id"],
        amount_usd      = info["amount_usd"],
        currency        = info["currency"],
        source          = "checkout",
    )

    return jsonify({"success": True, "plan": captured_plan})


# ---------------------------------------------------------------------------
# POST /checkout/webhook  — PayPal event webhook (no session auth)
# ---------------------------------------------------------------------------
@checkout_bp.route("/webhook", methods=["POST"])
@csrf.exempt
def webhook():
    # Read raw body BEFORE any JSON parsing (needed for signature verification)
    raw_body = request.get_data()

    # Verify PayPal's signature — reject unverified events in production
    is_verified = verify_webhook_signature(request.headers, raw_body)
    if not is_verified:
        paypal_mode = os.environ.get("PAYPAL_MODE", "sandbox").lower()
        if paypal_mode == "live":
            logger.warning("Webhook rejected — signature verification failed.")
            return jsonify({"error": "Signature verification failed."}), 401
        # In sandbox mode allow unverified events so local testing works without
        # configuring a real webhook ID.  Log a warning so it is visible.
        logger.warning(
            "Webhook signature not verified (sandbox mode — proceeding anyway). "
            "Set PAYPAL_WEBHOOK_ID for verified processing."
        )

    try:
        event = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        logger.error("Webhook body parse error: %s", exc)
        return jsonify({"error": "Invalid JSON body."}), 400

    event_type = event.get("event_type", "")
    logger.info("Webhook received | event_type=%s", event_type)

    # Handle order completion (most complete event — has all purchase_unit data)
    if event_type == "CHECKOUT.ORDER.COMPLETED":
        _handle_order_completed(event)

    # Handle capture completion as a secondary / backup event
    elif event_type == "PAYMENT.CAPTURE.COMPLETED":
        _handle_capture_completed(event)

    # PayPal expects a 200 quickly regardless of whether we acted on the event
    return jsonify({"status": "ok"}), 200


def _handle_order_completed(event: dict) -> None:
    resource = event.get("resource", {})
    order_id = resource.get("id", "")
    status   = resource.get("status", "")

    if status != "COMPLETED":
        logger.info("Webhook order not COMPLETED (status=%s) — ignoring.", status)
        return

    info = extract_order_info(resource)

    # Resolve the user: first from our pending purchase record, then from custom_id
    purchase = Purchase.query.filter_by(paypal_order_id=order_id).first()
    if purchase:
        user = User.query.get(purchase.user_id)
    else:
        # Fallback: parse custom_id "plan|user_id"
        raw_custom = ""
        for pu in resource.get("purchase_units", []):
            raw_custom = pu.get("custom_id", "")
            if raw_custom:
                break
        parts   = raw_custom.split("|")
        user_id = int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else None
        user    = User.query.get(user_id) if user_id else None

    if not user:
        logger.error("Webhook cannot resolve user | order_id=%s", order_id)
        return

    plan = info["plan"] if info["plan"] in PLAN_PRICES else (
        purchase.plan if purchase else None
    )
    if not plan:
        logger.error("Webhook cannot determine plan | order_id=%s", order_id)
        return

    _fulfill_purchase(
        user              = user,
        plan              = plan,
        paypal_order_id   = info["order_id"],
        paypal_capture_id = info["capture_id"],
        amount_usd        = info["amount_usd"],
        currency          = info["currency"],
        source            = "webhook",
    )


def _handle_capture_completed(event: dict) -> None:
    resource   = event.get("resource", {})
    capture_id = resource.get("id", "")
    status     = resource.get("status", "")

    if status != "COMPLETED":
        return

    # Extract order_id from supplementary data
    order_id = (
        resource
        .get("supplementary_data", {})
        .get("related_ids", {})
        .get("order_id", "")
    )
    if not order_id:
        logger.warning("PAYMENT.CAPTURE.COMPLETED missing order_id | capture_id=%s", capture_id)
        return

    # Look up the pending purchase to get user and plan
    purchase = Purchase.query.filter_by(paypal_order_id=order_id).first()
    if not purchase:
        logger.warning("No pending purchase for order_id=%s in PAYMENT.CAPTURE.COMPLETED", order_id)
        return

    user = User.query.get(purchase.user_id)
    if not user:
        logger.error("User not found | user_id=%s order_id=%s", purchase.user_id, order_id)
        return

    amount     = resource.get("amount", {})
    amount_usd = amount.get("value", str(purchase.amount_usd))
    currency   = amount.get("currency_code", purchase.currency)

    _fulfill_purchase(
        user              = user,
        plan              = purchase.plan,
        paypal_order_id   = order_id,
        paypal_capture_id = capture_id,
        amount_usd        = amount_usd,
        currency          = currency,
        source            = "webhook",
    )


# ---------------------------------------------------------------------------
# GET /checkout/success  — post-payment landing page
# ---------------------------------------------------------------------------
@checkout_bp.route("/success")
@login_required
def success():
    return render_template(
        "checkout/success.html",
        plan       = current_user.plan,
        plan_label = PLAN_LABELS.get(current_user.plan, ""),
    )
