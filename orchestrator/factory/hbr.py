"""HBR (Host-Based Resource manager): daily cost budget and per-model usage tracking.

Tracks cumulative spend within a calendar day against a configurable USD budget.
When the budget is exhausted, ``can_allocate_sandbox()`` returns False, and the
assign node + scheduler bot both refuse to start new work until midnight reset.

``record_cost`` is a sync method safe to call from async LangGraph nodes
(runs in a single-threaded asyncio event loop; no cross-thread access).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class HBRManager:
    """Daily spend tracker and sandbox budget gate.

    Args:
        daily_budget_usd: Maximum USD to spend per calendar day.
            Zero (the default) disables the budget gate — ``can_allocate_sandbox``
            always returns True.
    """

    def __init__(self, daily_budget_usd: float = 0.0) -> None:
        self._daily_budget_usd = daily_budget_usd
        self._daily_cost_usd: float = 0.0
        self._model_usage: dict[str, dict[str, Any]] = {}
        self._budget_day: date = date.today()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _reset_if_new_day(self) -> None:
        today = date.today()
        if today != self._budget_day:
            logger.info(
                "hbr: new day (%s) — resetting daily cost (was $%.4f)",
                today,
                self._daily_cost_usd,
            )
            self._daily_cost_usd = 0.0
            self._model_usage = {}
            self._budget_day = today

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def can_allocate_sandbox(self) -> bool:
        """Return True if starting a new sandbox is within the daily budget.

        Always returns True when no daily budget is configured
        (``daily_budget_usd <= 0``).
        """
        self._reset_if_new_day()
        if self._daily_budget_usd > 0:
            return self._daily_cost_usd < self._daily_budget_usd
        return True

    def record_cost(
        self,
        model: str,
        cost_usd: float,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        """Accumulate spend for a model invocation or sandbox run.

        Args:
            model: Model identifier (e.g. ``"MiniMax-M2.7"``) or ``"e2b"`` for
                sandbox compute charges.
            cost_usd: Cost in USD to add to today's total.
            input_tokens: Input token count (0 for sandbox-only charges).
            output_tokens: Output token count (0 for sandbox-only charges).
        """
        if cost_usd <= 0:
            return
        self._reset_if_new_day()
        self._daily_cost_usd += cost_usd
        entry = self._model_usage.setdefault(
            model, {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        )
        entry["input_tokens"] += input_tokens
        entry["output_tokens"] += output_tokens
        entry["cost_usd"] += cost_usd

    def budget_remaining_usd(self) -> float | None:
        """Remaining daily budget in USD, or None when budget is disabled."""
        if self._daily_budget_usd <= 0:
            return None
        self._reset_if_new_day()
        return max(0.0, self._daily_budget_usd - self._daily_cost_usd)

    def status(self) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot of current resource state."""
        self._reset_if_new_day()
        tomorrow_dt = datetime.combine(
            date.fromordinal(self._budget_day.toordinal() + 1),
            datetime.min.time(),
            tzinfo=timezone.utc,
        )
        remaining = self.budget_remaining_usd()
        return {
            "daily_cost_usd": round(self._daily_cost_usd, 6),
            "daily_budget_usd": self._daily_budget_usd,
            "budget_remaining_usd": (
                round(remaining, 6) if remaining is not None else None
            ),
            "budget_enabled": self._daily_budget_usd > 0,
            "budget_reset_at": tomorrow_dt.isoformat(),
            "model_usage": {
                model: {
                    "input_tokens": v["input_tokens"],
                    "output_tokens": v["output_tokens"],
                    "cost_usd": round(v["cost_usd"], 6),
                }
                for model, v in self._model_usage.items()
            },
        }


_hbr_instance: HBRManager | None = None


def get_hbr() -> HBRManager:
    """Return the process-level HBR singleton, initialised on first call."""
    global _hbr_instance
    if _hbr_instance is None:
        from .config import get_settings

        settings = get_settings()
        _hbr_instance = HBRManager(daily_budget_usd=settings.daily_budget_usd)
        logger.info(
            "hbr: initialised (daily_budget_usd=%.2f)", settings.daily_budget_usd
        )
    return _hbr_instance
