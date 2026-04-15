"""
PayPal Orders API v2 client.

Required environment variables:
    PAYPAL_CLIENT_ID      — PayPal application client ID (safe to expose to browser)
    PAYPAL_CLIENT_SECRET  — PayPal application client secret (server-side only)
    PAYPAL_MODE           — 'sandbox' or 'live'  (default: 'sandbox')
    PAYPAL_WEBHOOK_ID     — Webhook ID from the PayPal developer dashboard
                            (required for webhook signature verification)

Prices are defined here exclusively.  They are never read from the client.
"""
from __future__ import annotations

import json
import os
from typing import Any

import requests as _requests

from utils.logging_setup import get_logger

logger = get_logger()

# ---------------------------------------------------------------------------
# Server-side plan registry — single source of truth for prices
# ---------------------------------------------------------------------------
PLAN_PRICES: dict[str, str] = {
    "beginner": "99.90",
    "pro":      "149.90",
}

PLAN_LABELS: dict[str, str] = {
    "beginner": "Decision Weapon — Beginner (Lifetime Access)",
    "pro":      "Decision Weapon — Pro (Lifetime Access)",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _base_url() -> str:
    mode = os.environ.get("PAYPAL_MODE", "sandbox").strip().lower()
    return (
        "https://api-m.paypal.com"
        if mode == "live"
        else "https://api-m.sandbox.paypal.com"
    )


def _get_access_token() -> str:
    """Obtain a Bearer token from PayPal using client credentials."""
    client_id     = os.environ.get("PAYPAL_CLIENT_ID",     "")
    client_secret = os.environ.get("PAYPAL_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise RuntimeError(
            "PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET environment variables must be set."
        )
    resp = _requests.post(
        f"{_base_url()}/v1/oauth2/token",
        auth=(client_id, client_secret),
        data={"grant_type": "client_credentials"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _auth_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_order(plan: str, user_id: int) -> dict[str, Any]:
    """
    Create a PayPal order for the given plan.

    The plan name and user ID are embedded in custom_id so the webhook can
    identify the purchase without a database lookup as a last resort.

    Returns the full PayPal Orders API response dict.
    Raises KeyError for unrecognised plan, HTTPError on PayPal API failure.
    """
    if plan not in PLAN_PRICES:
        raise KeyError(f"Unrecognised plan: {plan!r}")

    token  = _get_access_token()
    amount = PLAN_PRICES[plan]

    body = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "description": PLAN_LABELS[plan],
                "custom_id":   f"{plan}|{user_id}",   # parsed by webhook fallback
                "amount": {
                    "currency_code": "USD",
                    "value":         amount,
                },
            }
        ],
    }

    resp = _requests.post(
        f"{_base_url()}/v2/checkout/orders",
        json=body,
        headers={**_auth_headers(token), "Prefer": "return=representation"},
        timeout=15,
    )
    resp.raise_for_status()
    order = resp.json()
    logger.info(
        "PayPal order created | order_id=%s plan=%s amount=%s user_id=%s",
        order.get("id"), plan, amount, user_id,
    )
    return order


def capture_order(paypal_order_id: str) -> dict[str, Any]:
    """
    Capture an approved PayPal order.

    Returns the full capture response dict.
    Raises HTTPError on PayPal API failure.
    """
    token = _get_access_token()
    resp  = _requests.post(
        f"{_base_url()}/v2/checkout/orders/{paypal_order_id}/capture",
        headers={**_auth_headers(token), "Prefer": "return=representation"},
        timeout=15,
    )
    resp.raise_for_status()
    result = resp.json()
    logger.info(
        "PayPal order captured | order_id=%s status=%s",
        paypal_order_id, result.get("status"),
    )
    return result


def verify_webhook_signature(flask_headers: dict, raw_body: bytes) -> bool:
    """
    Verify a PayPal webhook event using PayPal's Notifications API.

    flask_headers  — Flask request.headers (case-insensitive dict)
    raw_body       — exact bytes from request.get_data() before any parsing

    Returns True only if PayPal confirms the signature as valid.
    Returns False (with a warning log) if PAYPAL_WEBHOOK_ID is not configured,
    allowing callers to decide whether to reject or process unverified events.
    """
    webhook_id = os.environ.get("PAYPAL_WEBHOOK_ID", "").strip()
    if not webhook_id:
        logger.warning(
            "PAYPAL_WEBHOOK_ID not configured — cannot verify webhook signature. "
            "Set this variable in production to prevent spoofed events."
        )
        return False

    try:
        token       = _get_access_token()
        event_body  = json.loads(raw_body.decode("utf-8"))

        payload = {
            "auth_algo":         flask_headers.get("PAYPAL-AUTH-ALGO",        ""),
            "cert_url":          flask_headers.get("PAYPAL-CERT-URL",          ""),
            "transmission_id":   flask_headers.get("PAYPAL-TRANSMISSION-ID",   ""),
            "transmission_sig":  flask_headers.get("PAYPAL-TRANSMISSION-SIG",  ""),
            "transmission_time": flask_headers.get("PAYPAL-TRANSMISSION-TIME", ""),
            "webhook_id":        webhook_id,
            "webhook_event":     event_body,
        }

        resp = _requests.post(
            f"{_base_url()}/v1/notifications/verify-webhook-signature",
            json=payload,
            headers=_auth_headers(token),
            timeout=10,
        )
        resp.raise_for_status()
        verification_status = resp.json().get("verification_status", "")
        logger.info("PayPal webhook verification result: %s", verification_status)
        return verification_status == "SUCCESS"

    except Exception as exc:
        logger.exception("Webhook signature verification failed: %s", exc)
        return False


def extract_order_info(capture_response: dict[str, Any]) -> dict[str, Any]:
    """
    Pull the fields we care about from a PayPal capture (or webhook) response.

    Returns:
        order_id, capture_id, status, plan, amount_usd, currency
    """
    order_id = capture_response.get("id", "")
    status   = capture_response.get("status", "")

    purchase_units = capture_response.get("purchase_units") or [{}]
    pu             = purchase_units[0]

    captures   = (pu.get("payments") or {}).get("captures") or [{}]
    cap        = captures[0]
    capture_id = cap.get("id", "")

    amount     = pu.get("amount") or {}
    amount_usd = amount.get("value", "0.00")
    currency   = amount.get("currency_code", "USD")

    # custom_id was set to "plan|user_id" at order creation
    raw_custom = pu.get("custom_id", "")
    plan = raw_custom.split("|")[0] if "|" in raw_custom else raw_custom

    return {
        "order_id":   order_id,
        "capture_id": capture_id,
        "status":     status,
        "plan":       plan,
        "amount_usd": amount_usd,
        "currency":   currency,
    }
