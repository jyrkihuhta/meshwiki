"""Generic base class and result type for periodic background bots."""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

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
    """

    name: str = "unnamed-bot"
    interval_seconds: int = 300

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

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

    async def _loop(self) -> None:
        """Internal scheduling loop: run → sleep → repeat until stopped."""
        while not self._stop_event.is_set():
            started = time.monotonic()
            try:
                result = await self.run()
                elapsed = time.monotonic() - started
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
