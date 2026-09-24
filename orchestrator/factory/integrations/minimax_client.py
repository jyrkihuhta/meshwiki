"""MiniMax token-plan usage client for quota monitoring."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

from ..config import get_settings

logger = logging.getLogger(__name__)

_USAGE_URL = "https://www.minimax.io/v1/token_plan/remains"


@dataclass
class TokenPlanStatus:
    """Result of a MiniMax token plan quota check.

    ``remaining`` is ``None`` when the value could not be determined (API
    error, missing key, unrecognised response shape).  The scheduler treats
    ``None`` as "unknown — allow dispatch" so a broken usage-check never
    stalls the whole factory.
    """

    remaining: int | None
    limit: int | None
    reset_at: str | None
    raw: dict = field(default_factory=dict)


class MiniMaxUsageClient:
    """Async HTTP client for the MiniMax token-plan usage API.

    Uses the same ``FACTORY_MINIMAX_API_KEY`` credential as the grinder
    sandboxes — no separate secret needed.
    """

    def __init__(self, api_key: str | None = None) -> None:
        settings = get_settings()
        self._api_key = api_key or settings.minimax_api_key
        self._client = httpx.AsyncClient(timeout=10.0)

    async def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._client.aclose()

    async def __aenter__(self) -> "MiniMaxUsageClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def get_token_plan_remaining(self) -> TokenPlanStatus:
        """Query the MiniMax token-plan endpoint for remaining quota.

        Returns:
            :class:`TokenPlanStatus` with ``remaining=None`` on any error so
            callers can safely treat failures as "quota unknown".
        """
        if not self._api_key:
            logger.debug("minimax: no API key — skipping quota check")
            return TokenPlanStatus(remaining=None, limit=None, reset_at=None)

        try:
            resp = await self._client.get(
                _USAGE_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            resp.raise_for_status()
            data: dict = resp.json()
        except Exception as exc:
            logger.warning("minimax: usage API call failed: %s", exc)
            return TokenPlanStatus(remaining=None, limit=None, reset_at=None)

        # Probe several plausible field-name variants (undocumented schema).
        remaining: int | None = (
            data.get("remaining_tokens")
            or data.get("remaining")
            or data.get("tokens_remaining")
        )
        limit: int | None = (
            data.get("total_tokens") or data.get("limit") or data.get("tokens_limit")
        )
        reset_at: str | None = (
            data.get("reset_at") or data.get("reset_time") or data.get("next_reset")
        )

        if remaining is None:
            logger.warning(
                "minimax: could not parse remaining tokens from response: %s", data
            )

        return TokenPlanStatus(
            remaining=remaining, limit=limit, reset_at=reset_at, raw=data
        )
