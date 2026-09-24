"""Generic base class and result type for periodic background bots."""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class BotResult:
    """Result returned by a single bot run."""

    ran_at: float  # time.monotonic() timestamp when the run started
    actions_taken: int = 0
    errors: list[str] = field(default_factory=list)
    details: str = ""


class BaseBot(ABC):
    """Abstract base class for all periodic bots.

    Subclasses must define:
    - ``name`` — human-readable identifier used in logs
    - ``interval_seconds`` — how long to sleep between runs
    - ``run()`` — the async method that performs one reconciliation cycle

    The scheduling loop (``start`` / ``stop``) is provided here so each bot
    author only needs to implement ``run()``.  The loop catches all exceptions,
    logs them, and continues — it never crashes the server.

    After each run the loop calls ``_update_bot_page()`` (fire-and-forget) to
    keep a wiki page at ``Factory/Bots/<name>`` up-to-date. Frontmatter is
    patched on every tick; the activity log section is only appended when the
    run produced actions or errors.
    """

    name: str = "unnamed-bot"
    interval_seconds: int = 300

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self.total_runs: int = 0
        self.total_actions: int = 0
        self.last_result: BotResult | None = None
        self._last_ran_wall: float | None = None

    @abstractmethod
    async def run(self) -> BotResult:
        """Execute one reconciliation cycle.

        Returns:
            A :class:`BotResult` describing what happened.
        """

    async def start(self) -> None:
        """Launch the scheduling loop as a background asyncio task."""
        if self._task is not None and not self._task.done():
            logger.warning("bot[%s]: already running — ignoring start()", self.name)
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop(), name=f"bot:{self.name}")
        logger.info("bot[%s]: started (interval=%ds)", self.name, self.interval_seconds)

    async def stop(self) -> None:
        """Signal the loop to stop and wait for it to finish."""
        self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        logger.info("bot[%s]: stopped", self.name)

    #: Bots that ONLY do work via Anthropic should set this True. When the
    #: shared circuit breaker is tripped, _loop() will skip their run cycle
    #: entirely and log a single quiet "paused" line per cycle instead of
    #: producing N back-to-back "failed to analyze" errors.
    pauses_on_anthropic_block: bool = False

    async def _loop(self) -> None:
        """Internal scheduling loop: run → sleep → repeat until stopped."""
        # Local import to avoid a cycle (pm_agent.py imports from state).
        from ..agents.pm_agent import (
            anthropic_block_reason,
            anthropic_blocked_seconds_remaining,
        )

        while not self._stop_event.is_set():
            started = time.monotonic()
            blocked_secs = anthropic_blocked_seconds_remaining()
            if self.pauses_on_anthropic_block and blocked_secs > 0:
                # Anthropic circuit breaker is tripped — this bot can't make
                # progress without an Anthropic call. Skip the cycle quietly
                # and check again next tick.
                self.last_result = BotResult(
                    ran_at=started,
                    actions_taken=0,
                    errors=[],
                    details=(
                        f"paused: Anthropic blocked for {blocked_secs:.0f}s more "
                        f"({anthropic_block_reason() or 'no reason recorded'})"
                    ),
                )
                logger.info(
                    "bot[%s]: paused — Anthropic blocked for %.0fs more",
                    self.name,
                    blocked_secs,
                )
            else:
                try:
                    result = await self.run()
                    elapsed = time.monotonic() - started
                    self.total_runs += 1
                    self.total_actions += result.actions_taken
                    self.last_result = result
                    self._last_ran_wall = time.time()
                    logger.info(
                        "bot[%s]: ran in %.2fs — actions=%d errors=%d%s",
                        self.name,
                        elapsed,
                        result.actions_taken,
                        len(result.errors),
                        f" details={result.details!r}" if result.details else "",
                    )
                    if result.errors:
                        for err in result.errors:
                            logger.warning("bot[%s]: error — %s", self.name, err)
                    await self._update_bot_page(result)
                except Exception as exc:  # noqa: BLE001
                    elapsed = time.monotonic() - started
                    logger.error(
                        "bot[%s]: unhandled exception after %.2fs: %s",
                        self.name,
                        elapsed,
                        exc,
                        exc_info=True,
                    )

            try:
                await asyncio.wait_for(
                    asyncio.shield(self._stop_event.wait()),
                    timeout=self.interval_seconds,
                )
                # If we get here the stop event fired before the timeout — exit.
                break
            except asyncio.TimeoutError:
                # Normal case: interval elapsed, go around again.
                pass

    def get_status(self) -> dict:
        """Return a serialisable status snapshot for the dashboard."""
        result = self.last_result
        return {
            "name": self.name,
            "interval_seconds": self.interval_seconds,
            "total_runs": self.total_runs,
            "total_actions": self.total_actions,
            "running": self._task is not None and not self._task.done(),
            "last_ran_at": self._last_ran_wall,
            "last_actions": result.actions_taken if result else None,
            "last_errors": result.errors if result else [],
            "last_details": result.details if result else "",
        }

    # ------------------------------------------------------------------
    # Wiki page maintenance
    # ------------------------------------------------------------------

    async def _update_bot_page(self, result: BotResult) -> None:
        """Upsert the wiki page at Factory/Bots/<name> after each run.

        Always patches frontmatter (cheap); only appends to the activity log
        when the run produced actions or errors to avoid log spam.
        """
        from ..integrations.meshwiki_client import MeshWikiClient, _patch_frontmatter

        page_name = f"Factory/Bots/{self.name}"
        ran_at_str = (
            datetime.fromtimestamp(self._last_ran_wall).isoformat(timespec="seconds")
            if self._last_ran_wall
            else ""
        )
        fm_updates = {
            "last_ran_at": ran_at_str,
            "total_runs": self.total_runs,
            "total_actions": self.total_actions,
            "last_details": result.details or "",
            "last_error_count": len(result.errors),
        }

        try:
            async with MeshWikiClient() as wiki:
                page = await wiki.get_page(page_name)

                if page is None:
                    content = self._initial_page_content(ran_at_str)
                else:
                    content = _patch_frontmatter(page["content"], fm_updates)

                is_exceptional = result.actions_taken > 0 or bool(result.errors)
                if is_exceptional:
                    content = _prepend_log_entry(
                        content, _format_log_entry(result, ran_at_str)
                    )

                await wiki.create_page(page_name, content)
        except Exception as exc:
            logger.debug(
                "bot[%s]: failed to update wiki page (non-critical): %s", self.name, exc
            )

    def _initial_page_content(self, ran_at_str: str) -> str:
        """Return the full content for a freshly created bot wiki page."""
        interval_human = _humanize_interval(self.interval_seconds)
        return (
            f"---\n"
            f"title: {self.name}\n"
            f"type: bot-status\n"
            f"interval_seconds: {self.interval_seconds}\n"
            f"last_ran_at: {ran_at_str}\n"
            f"total_runs: 1\n"
            f"total_actions: 0\n"
            f'last_details: ""\n'
            f"last_error_count: 0\n"
            f"---\n\n"
            f"# {self.name}\n\n"
            f"Runs every {interval_human}.\n\n"
            f"## Activity Log\n\n"
            f"*No exceptional activity yet.*\n"
        )


# ------------------------------------------------------------------
# Module-level helpers (pure functions, easy to test)
# ------------------------------------------------------------------


def _humanize_interval(seconds: int) -> str:
    """Return a human-readable interval string (e.g. '5 minutes', '1 week')."""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        m = seconds // 60
        return f"{m} minute{'s' if m != 1 else ''}"
    if seconds < 86400:
        h = seconds // 3600
        return f"{h} hour{'s' if h != 1 else ''}"
    if seconds < 604800:
        d = seconds // 86400
        return f"{d} day{'s' if d != 1 else ''}"
    w = seconds // 604800
    return f"{w} week{'s' if w != 1 else ''}"


def _format_log_entry(result: BotResult, ran_at_str: str) -> str:
    """Format a single activity log line from a BotResult."""
    icon = "⚠" if result.errors else "✓"
    parts = [f"**{ran_at_str}** — {icon} {result.actions_taken} actions"]
    if result.details:
        parts.append(result.details)
    if result.errors:
        error_summary = "; ".join(result.errors[:2])
        if len(result.errors) > 2:
            error_summary += f" (+{len(result.errors) - 2} more)"
        parts.append(f"{len(result.errors)} error(s): {error_summary}")
    return "- " + " — ".join(parts)


def _prepend_log_entry(content: str, entry: str, max_entries: int = 20) -> str:
    """Insert *entry* at the top of the Activity Log section (keep last max_entries)."""
    marker = "## Activity Log\n"
    idx = content.find(marker)
    if idx == -1:
        return content.rstrip() + f"\n\n## Activity Log\n\n{entry}\n"

    after = idx + len(marker)
    rest = content[after:].lstrip("\n")
    # Drop the empty-state placeholder if present
    rest = rest.replace("*No exceptional activity yet.*\n", "").lstrip("\n")

    existing = [line for line in rest.splitlines() if line.startswith("- ")]
    entries = [entry] + existing[: max_entries - 1]

    return content[:after] + "\n" + "\n".join(entries) + "\n"
