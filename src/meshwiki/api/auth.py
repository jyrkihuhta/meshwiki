"""API key authentication dependency for the agent factory JSON API."""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import meshwiki.config as cfg

_bearer = HTTPBearer(auto_error=False)


def require_api_key(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    """FastAPI dependency that enforces API key authentication.

    Behaviour:
    - ``factory_enabled=False`` → 503 (factory not enabled)
    - ``factory_api_key=""``    → open access (dev mode, no key required)
    - wrong / missing key       → 401
    """
    if not cfg.settings.factory_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Factory API not enabled",
        )
    if not cfg.settings.factory_api_key:
        return  # dev mode — no key configured
    if credentials is None or credentials.credentials != cfg.settings.factory_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
