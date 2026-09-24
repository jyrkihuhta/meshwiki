"""Purgatory bot — tier 1 (unit test) of deck's three-tier staging design.

See deck PLAN.md M2.5. Staging is split into three differently-cadenced
purposes: unit test (this bot — periodic regression check against
target-dummy's simple containers), state-of-the-art (a tracked benchmark
goalpost, not yet built), and usability (monitoring Molly itself, not yet
built). This bot owns only tier 1.

On each tick (daily by default) the bot:
1. Arms every tier-1 target-dummy handle.
2. Starts Molly's heartbeat and lets it run for a bounded window
   (``FACTORY_PURGATORY_RUN_WINDOW_SECONDS``, default 10 minutes) — tier 1
   is periodic, not continuously armed, by design.
3. Stops the heartbeat and disarms every target again.
4. Summarizes per-target coverage from Molly's ``/status`` and flags any
   target that matched zero checks as an error — that's the single most
   valuable signal this bot can produce: it's exactly the failure mode
   (discovery/playbook-matching silently broken) that took a full manual
   debugging session to catch the first time target-dummy was armed.

Reports to ``Factory/Bots/purgatory`` via the same BaseBot wiki-page
mechanism every other bot uses — deck reads that page directly (no need to
re-run anything by hand) to check whether tier 1 is actually passing.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from ..config import get_settings
from .base import BaseBot, BotResult

logger = logging.getLogger(__name__)

# The tier-1 handles as of deck PLAN.md M2.5 (target-dummy's six original
# apps + DVGA). Kept here rather than derived from Molly's /status so a
# target Molly doesn't currently know about (mid-deploy, config drift)
# still gets reported as a clear error instead of just silently skipped.
TIER1_TARGETS: tuple[str, ...] = (
    "notekeeper", "taskboard", "dashql", "couponshop", "ssrfbox", "jwtlab", "dvga",
)


class PurgatoryBot(BaseBot):
    """Daily bounded arm→run→disarm cycle against the tier-1 range."""

    name = "purgatory"
    pauses_on_anthropic_block = False  # talks to Molly's HTTP API, not an LLM

    def __init__(
        self,
        interval_seconds: int | None = None,
        run_window_seconds: int | None = None,
        molly_url: str | None = None,
    ) -> None:
        super().__init__()
        settings = get_settings()
        self.interval_seconds = (
            interval_seconds
            if interval_seconds is not None
            else settings.purgatory_interval_seconds
        )
        self._run_window_seconds = (
            run_window_seconds
            if run_window_seconds is not None
            else settings.purgatory_run_window_seconds
        )
        self._molly_url = (
            molly_url if molly_url is not None else (settings.molly_url or "http://molly:8780")
        ).rstrip("/")

    async def run(self) -> BotResult:
        started = time.monotonic()
        errors: list[str] = []

        # Arm every tier-1 target. Keep going on individual failures — a
        # bad target shouldn't block coverage on the rest, and each failure
        # is itself a signal worth reporting.
        armed = 0
        for handle in TIER1_TARGETS:
            try:
                await self._molly_post(f"/targets/{handle}/arm")
                armed += 1
            except Exception as e:
                errors.append(f"arm {handle}: {type(e).__name__}: {e}")

        if armed == 0:
            elapsed = time.monotonic() - started
            return BotResult(
                ran_at=started,
                actions_taken=0,
                errors=errors or ["could not arm any tier-1 target"],
                details="tier1 run aborted: 0/%d targets armed" % len(TIER1_TARGETS),
            )

        try:
            await self._molly_post("/heartbeat/start")
        except Exception as e:
            errors.append(f"heartbeat start: {type(e).__name__}: {e}")

        await asyncio.sleep(self._run_window_seconds)

        try:
            await self._molly_post("/heartbeat/stop")
        except Exception as e:
            errors.append(f"heartbeat stop: {type(e).__name__}: {e}")

        disarmed = 0
        for handle in TIER1_TARGETS:
            try:
                await self._molly_post(f"/targets/{handle}/disarm")
                disarmed += 1
            except Exception as e:
                errors.append(f"disarm {handle}: {type(e).__name__}: {e}")

        try:
            status = await self._molly_get("/status")
        except Exception as e:
            errors.append(f"final status fetch: {type(e).__name__}: {e}")
            status = None

        summary, summary_errors = summarize_coverage(status, TIER1_TARGETS)
        errors.extend(summary_errors)

        elapsed = time.monotonic() - started
        return BotResult(
            ran_at=started,
            actions_taken=1,
            errors=errors,
            details=(
                f"armed={armed}/{len(TIER1_TARGETS)} disarmed={disarmed}/{len(TIER1_TARGETS)} "
                f"window={self._run_window_seconds}s elapsed={elapsed:.0f}s — {summary}"
            ),
        )

    # ------------------------------------------------------------------
    # Molly API helpers
    # ------------------------------------------------------------------

    def _molly_headers(self) -> dict[str, str]:
        settings = get_settings()
        headers: dict[str, str] = {}
        if settings.molly_api_token:
            headers["Authorization"] = f"Bearer {settings.molly_api_token}"
        return headers

    async def _molly_post(self, path: str) -> dict:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(f"{self._molly_url}{path}", headers=self._molly_headers())
            r.raise_for_status()
            return r.json()

    async def _molly_get(self, path: str) -> dict:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(f"{self._molly_url}{path}", headers=self._molly_headers())
            r.raise_for_status()
            return r.json()


# ---------------------------------------------------------------------------
# Pure helper — kept outside the class so it's trivially unit-testable
# ---------------------------------------------------------------------------


def summarize_coverage(
    status: dict[str, Any] | None, expected_targets: tuple[str, ...]
) -> tuple[str, list[str]]:
    """Summarize Molly's /status coverage section into a short human string
    plus a list of error strings for any target that looks broken.

    A target that matched zero checks after a full run window is the
    highest-value signal this bot exists to produce — it's exactly the
    "discovery silently returns nothing" failure mode from deck PLAN.md
    M2's coverage-scheduler/seed-leaves incidents, and it's cheap to catch
    automatically instead of needing another multi-hour manual debug pass.

    Returns:
        (summary_string, error_strings)
    """
    if not status:
        return "no status available", ["final status fetch returned nothing"]

    coverage_targets = (
        status.get("heartbeat", {}).get("coverage", {}).get("targets", [])
    )
    by_handle = {t.get("handle"): t for t in coverage_targets}

    errors: list[str] = []
    total_checks = 0
    total_complete = 0
    total_confirmed = 0
    total_failed = 0
    zero_check_targets: list[str] = []
    missing_targets: list[str] = []

    for handle in expected_targets:
        t = by_handle.get(handle)
        if t is None:
            missing_targets.append(handle)
            continue
        checks = t.get("checks") or {}
        matched = checks.get("total", 0)
        total_checks += matched
        total_complete += checks.get("complete", 0)
        total_confirmed += checks.get("confirmed", 0)
        total_failed += checks.get("failed", 0)
        if matched == 0 and t.get("status") not in ("not_discovered",):
            # not_discovered just means it hasn't had a DISCOVER tick yet
            # this run (round-robin, bounded window) — not necessarily
            # broken. A target that WAS discovered but matched 0 checks is
            # the real signal: it got a chance and found nothing.
            zero_check_targets.append(handle)

    if missing_targets:
        errors.append(
            f"targets missing from Molly's config entirely: {', '.join(missing_targets)}"
        )
    if zero_check_targets:
        errors.append(
            f"discovered but matched 0 checks (likely broken discovery/matching): "
            f"{', '.join(zero_check_targets)}"
        )

    summary = (
        f"{len(expected_targets) - len(missing_targets)}/{len(expected_targets)} targets known, "
        f"{total_checks} checks matched, {total_complete} complete, "
        f"{total_confirmed} confirmed findings, {total_failed} failed"
    )
    return summary, errors
