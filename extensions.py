"""
Shared Flask extension instances.

Imported by app.py (init_app) and by blueprint modules (decorators).
Keeping them here avoids circular imports.
"""
from __future__ import annotations

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_mail import Mail
from flask_wtf.csrf import CSRFProtect


def _rate_limit_key() -> str:
    """
    Use the authenticated user's ID as the rate-limit key when available,
    falling back to the remote IP for anonymous requests.

    This prevents a single user behind a shared IP (corporate NAT, VPN) from
    being throttled by another user's activity, and stops a single user from
    bypassing per-IP limits by cycling IPs.
    """
    try:
        from flask_login import current_user
        if current_user and current_user.is_authenticated:
            return f"user:{current_user.id}"
    except Exception:
        pass
    return get_remote_address()


csrf    = CSRFProtect()
limiter = Limiter(key_func=_rate_limit_key, default_limits=[])
mail    = Mail()
