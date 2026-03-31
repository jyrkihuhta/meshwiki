"""Single-user password authentication middleware."""

import secrets
import time
from collections import defaultdict
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

# Paths that never require authentication
_PUBLIC_PATHS = frozenset({"/login", "/health/live", "/health/ready", "/metrics"})
_PUBLIC_PREFIXES = ("/static/", "/api/v1/")

# In-memory rate limiter: ip -> (fail_count, lockout_until)
_login_attempts: dict[str, tuple[int, float]] = defaultdict(lambda: (0, 0.0))
_MAX_ATTEMPTS = 5
_LOCKOUT_SECONDS = 600  # 10 minutes


def is_rate_limited(ip: str) -> bool:
    """Return True if this IP is currently locked out."""
    count, lockout_until = _login_attempts[ip]
    if lockout_until and lockout_until > time.monotonic():
        return True
    if lockout_until and lockout_until <= time.monotonic():
        _login_attempts[ip] = (0, 0.0)
    return False


def record_failed_attempt(ip: str) -> None:
    """Record a failed login; lock out after _MAX_ATTEMPTS."""
    count, _ = _login_attempts[ip]
    count += 1
    lockout_until = (
        time.monotonic() + _LOCKOUT_SECONDS if count >= _MAX_ATTEMPTS else 0.0
    )
    _login_attempts[ip] = (count, lockout_until)


def reset_attempts(ip: str) -> None:
    """Clear failed attempts after a successful login."""
    _login_attempts[ip] = (0, 0.0)


def verify_password(candidate: str, correct: str) -> bool:
    """Constant-time password comparison."""
    return secrets.compare_digest(candidate.encode(), correct.encode())


class AuthMiddleware(BaseHTTPMiddleware):
    """Redirect unauthenticated requests to /login."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        if path in _PUBLIC_PATHS or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return await call_next(request)
        if request.session.get("authenticated"):
            return await call_next(request)
        return RedirectResponse(url="/login", status_code=302)
