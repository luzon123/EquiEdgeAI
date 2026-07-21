import json
import os
import logging
from decimal import Decimal
from flask import Blueprint, render_template, request, jsonify, abort
from flask_login import login_required, current_user

# ---------------------------------------------------------------------------
# Imports from your application modules
# ---------------------------------------------------------------------------
try:
    from app.extensions import db, limiter, csrf
    from app.models import User, Purchase
    from app.decorators import _api_login_required
    from app.services.paypal import (
        pp_create_order,
        pp_capture_order,
        extract_order_info,
        verify_webhook_signature,
    )
    from app.constants import (
        PLAN_PRICES,
        PLAN_LABELS,
        CREDIT_PACKS,
        _PLAN_RANK,
    )
except ImportError:
    # Fallback/Local imports if structured relative to blueprint
    from .extensions import db, limiter, csrf  # type: ignore
    from .models import User, Purchase  # type: ignore
    from .decorators import _api_login_required  # type: ignore
    from .paypal import (  # type: ignore
        pp_create_order,
        pp_capture_order,
        extract_order_info,
        verify_webhook_signature,
    )
    from .constants import (  # type: ignore
        PLAN_PRICES,
        PLAN_LABELS,
        CREDIT_PACKS,
        _PLAN_RANK,
    )

logger = logging.getLogger(__name__)

checkout_bp = Blueprint("checkout", __name__, url_prefix="/checkout")


# ---------------------------------------------------------------------------
# Helper function for fulfilling purchases
# ---------------------------------------------------------------------------
def _fulfill_purchase(
    user: User,
    plan: str,
    paypal_order_id: str,
    paypal_capture_id: str,
    amount_usd: str | Decimal,
    currency: str,
    source: str,
) -> None:
    """Fulfills a purchase idempotently by updating user credits/plan and recording the purchase."""
    purchase = Purchase.query.filter_by(paypal_order_id=paypal_order_id).first()

    # Idempotency check: if already completed, do nothing
    if purchase and purchase.status == "completed":
        return

    if not purchase:
        purchase = Purchase(
            user_id=user.id,
            plan=plan,
            amount_usd=Decimal(str(amount_usd)),
            currency=currency,
            paypal_order_id=paypal_order_id,
            source=source,
        )
        db.session.add(purchase)

    purchase.paypal_capture_id = paypal_capture_id
    purchase.status = "completed"

    # Fulfill benefits based on plan type
    if plan in CREDIT_PACKS:
        current_credits = getattr(user, "credits", 0)
        user.credits = current_credits + CREDIT_PACKS[plan]
    else:
        user.plan = plan

    try:
        db.session.commit()
        logger.info(
            "Purchase fulfilled successfully | user_id=%s plan=%s order_id=%s source=%s",
            user.id, plan, paypal_order_id, source
        )
    except Exception as exc:
        db.session.rollback()
        logger.exception("Failed to commit purchase fulfillment: %s", exc)
        raise


# ---------------------------------------------------------------------------
# GET /checkout/<plan> — render checkout page
# ---------------------------------------------------------------------------
@checkout_bp.route("/<plan>")
@login_required
def checkout_page(plan: str):
    if plan not in PLAN_PRICES:
        abort(404)

    paypal_client_id = os.environ.get("PAYPAL_CLIENT_ID", "")
    if not paypal_client_id:
        logger.error("PAYPAL_CLIENT_ID not configured — checkout unavailable.")
        abort(500)

    return render_template(
        "checkout/checkout.html",
        plan             = plan,
        plan_label       = PLAN_LABELS.get(plan, plan),
        plan_price       = PLAN_PRICES[plan],
        paypal_client_id = paypal_client_id,
        paypal_mode      = os.environ.get("PAYPAL_MODE", "sandbox"),
    )


# ---------------------------------------------------------------------------
# POST /checkout/create-order — create a PayPal order (AJAX)
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
            amount_usd      = Decimal(str(PLAN_PRICES[plan])),
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
# POST /checkout/capture-order — capture approved order (AJAX)
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

    # Re-validate plan from PayPal's response
    captured_plan = info["plan"] if info["plan"] in PLAN_PRICES else (
        pending.plan if pending else None
    )
    if not captured_plan:
        logger.error("Cannot determine plan from capture response | order_id=%s", paypal_order_id)
        return jsonify({"error": "Could not verify purchased plan. Contact support."}), 500

    _fulfill_purchase(
        user              = current_user,
        plan              = captured_plan,
        paypal_order_id   = info["order_id"],
        paypal_capture_id = info["capture_id"],
        amount_usd        = info["amount_usd"],
        currency          = info["currency"],
        source            = "checkout",
    )

    return jsonify({"success": True, "plan": captured_plan})


# ---------------------------------------------------------------------------
# POST /checkout/webhook — PayPal event webhook (no session auth)
# ---------------------------------------------------------------------------
@checkout_bp.route("/webhook", methods=["POST"])
@csrf.exempt
def webhook():
    raw_body = request.get_data()

    is_verified = verify_webhook_signature(request.headers, raw_body)
    if not is_verified:
        paypal_mode = os.environ.get("PAYPAL_MODE", "sandbox").lower()
        if paypal_mode == "live":
            logger.warning("Webhook rejected — signature verification failed.")
            return jsonify({"error": "Signature verification failed."}), 401
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

    if event_type == "CHECKOUT.ORDER.COMPLETED":
        _handle_order_completed(event)
    elif event_type == "PAYMENT.CAPTURE.COMPLETED":
        _handle_capture_completed(event)

    return jsonify({"status": "ok"}), 200


def _handle_order_completed(event: dict) -> None:
    resource = event.get("resource", {})
    order_id = resource.get("id", "")
    status   = resource.get("status", "")

    if status != "COMPLETED":
        logger.info("Webhook order not COMPLETED (status=%s) — ignoring.", status)
        return

    info = extract_order_info(resource)

    purchase = Purchase.query.filter_by(paypal_order_id=order_id).first()
    if purchase:
        user = User.query.get(purchase.user_id)
    else:
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

    order_id = (
        resource
        .get("supplementary_data", {})
        .get("related_ids", {})
        .get("order_id", "")
    )
    if not order_id:
        logger.warning("PAYMENT.CAPTURE.COMPLETED missing order_id | capture_id=%s", capture_id)
        return

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
# GET /checkout/success — post-payment landing page
# ---------------------------------------------------------------------------
@checkout_bp.route("/success")
@login_required
def success():
    return render_template(
        "checkout/success.html",
        plan       = current_user.plan,
        plan_label = PLAN_LABELS.get(current_user.plan, ""),
    )
