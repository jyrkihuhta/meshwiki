"""Tests for the generic bot framework (BaseBot + BotRegistry)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from factory.bots.base import BaseBot, BotResult
from factory.bots.registry import BotRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _CountingBot(BaseBot):
    """Minimal bot that increments a counter on each run."""

    name = "counting"
    interval_seconds = 1

    def __init__(self) -> None:
        super().__init__()
        self.run_count = 0

    async def run(self) -> BotResult:
        self.run_count += 1
        return BotResult(ran_at=0.0, actions_taken=1)


class _FailingBot(BaseBot):
    """Bot whose run() raises unconditionally."""

    name = "failing"
    interval_seconds = 1

    async def run(self) -> BotResult:
        raise RuntimeError("boom")


class _MockRunBot(BaseBot):
    """Bot whose run() is replaced by a mock at test time."""

    name = "mock-run"
    interval_seconds = 1

    async def run(self) -> BotResult:  # pragma: no cover — replaced by mock
        return BotResult(ran_at=0.0)


# ---------------------------------------------------------------------------
# BotResult
# ---------------------------------------------------------------------------


def test_bot_result_defaults() -> None:
    """BotResult defaults are sane."""
    result = BotResult(ran_at=1.0)
    assert result.actions_taken == 0
    assert result.errors == []
    assert result.details == ""


def test_bot_result_with_values() -> None:
    """BotResult stores provided values."""
    result = BotResult(ran_at=2.0, actions_taken=3, errors=["oops"], details="hi")
    assert result.actions_taken == 3
    assert result.errors == ["oops"]
    assert result.details == "hi"


# ---------------------------------------------------------------------------
# BaseBot scheduling loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bot_runs_at_least_once() -> None:
    """A started bot calls run() at least once."""
    bot = _CountingBot()
    await bot.start()
    # Give the loop a moment to run
    await asyncio.sleep(0.05)
    await bot.stop()
    assert bot.run_count >= 1


@pytest.mark.asyncio
async def test_bot_stops_cleanly() -> None:
    """stop() returns without error and the task is done."""
    bot = _CountingBot()
    await bot.start()
    await asyncio.sleep(0.05)
    await bot.stop()
    assert bot._task is None or bot._task.done()


@pytest.mark.asyncio
async def test_bot_run_called_on_interval() -> None:
    """With a 1-second interval the bot runs multiple times in ~2.5s.

    We only assert ≥ 2 runs (not an exact count) to avoid flakiness on slow CI.
    """
    bot = _CountingBot()
    await bot.start()
    await asyncio.sleep(2.5)
    await bot.stop()
    assert bot.run_count >= 2


@pytest.mark.asyncio
async def test_failing_bot_does_not_crash_loop() -> None:
    """A bot whose run() raises must not crash the scheduler loop."""
    bot = _FailingBot()
    await bot.start()
    # Let the loop tick a few times — if it crashes, the task will be done early.
    await asyncio.sleep(2.5)
    # The loop task should still be alive (or just stopped by stop()).
    assert bot._task is not None and not bot._task.done()
    await bot.stop()


@pytest.mark.asyncio
async def test_start_idempotent() -> None:
    """Calling start() twice does not launch a second task."""
    bot = _CountingBot()
    await bot.start()
    task1 = bot._task
    await bot.start()  # second call should be a no-op
    task2 = bot._task
    assert task1 is task2
    await bot.stop()


@pytest.mark.asyncio
async def test_bot_run_called_with_mock() -> None:
    """Mock run() is awaited at least once by the scheduler loop."""
    bot = _MockRunBot()
    mock_run = AsyncMock(return_value=BotResult(ran_at=0.0, actions_taken=5))
    bot.run = mock_run  # type: ignore[method-assign]

    await bot.start()
    await asyncio.sleep(0.1)
    await bot.stop()

    assert mock_run.await_count >= 1


# ---------------------------------------------------------------------------
# BotRegistry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registry_start_all_starts_all_bots() -> None:
    """start_all() starts every registered bot."""
    bot1 = _CountingBot()
    bot2 = _CountingBot()
    registry = BotRegistry()
    registry.register(bot1)
    registry.register(bot2)

    await registry.start_all()
    await asyncio.sleep(0.1)
    await registry.stop_all()

    assert bot1.run_count >= 1
    assert bot2.run_count >= 1


@pytest.mark.asyncio
async def test_registry_stop_all_stops_all_bots() -> None:
    """stop_all() stops every registered bot cleanly."""
    bot1 = _CountingBot()
    bot2 = _CountingBot()
    registry = BotRegistry()
    registry.register(bot1)
    registry.register(bot2)

    await registry.start_all()
    await asyncio.sleep(0.1)
    await registry.stop_all()

    for bot in (bot1, bot2):
        assert bot._task is None or bot._task.done()


@pytest.mark.asyncio
async def test_registry_empty_start_stop() -> None:
    """An empty registry can be started and stopped without error."""
    registry = BotRegistry()
    await registry.start_all()
    await registry.stop_all()


def test_registry_register() -> None:
    """register() adds bots in order."""
    registry = BotRegistry()
    b1 = _CountingBot()
    b2 = _FailingBot()
    registry.register(b1)
    registry.register(b2)
    assert registry._bots == [b1, b2]
