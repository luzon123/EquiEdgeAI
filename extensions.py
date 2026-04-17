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

csrf    = CSRFProtect()
limiter = Limiter(key_func=get_remote_address, default_limits=[])
mail    = Mail()
