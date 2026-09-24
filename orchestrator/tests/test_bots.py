"""Tests for the generic bot framework (BaseBot + BotRegistry)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from factory.bots.base import (
    BaseBot,
    BotResult,
    _format_log_entry,
    _humanize_interval,
    _prepend_log_entry,
)
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


# ---------------------------------------------------------------------------
# _humanize_interval
# ---------------------------------------------------------------------------


def test_humanize_interval_seconds() -> None:
    assert _humanize_interval(30) == "30s"


def test_humanize_interval_minutes() -> None:
    assert _humanize_interval(300) == "5 minutes"
    assert _humanize_interval(60) == "1 minute"


def test_humanize_interval_hours() -> None:
    assert _humanize_interval(3600) == "1 hour"
    assert _humanize_interval(7200) == "2 hours"


def test_humanize_interval_days() -> None:
    assert _humanize_interval(86400) == "1 day"
    assert _humanize_interval(172800) == "2 days"


def test_humanize_interval_weeks() -> None:
    assert _humanize_interval(604800) == "1 week"


# ---------------------------------------------------------------------------
# _format_log_entry
# ---------------------------------------------------------------------------


def test_format_log_entry_success() -> None:
    result = BotResult(ran_at=0.0, actions_taken=3, details="rescued 3 tasks")
    entry = _format_log_entry(result, "2025-01-15T10:30:00")
    assert "✓" in entry
    assert "3 actions" in entry
    assert "rescued 3 tasks" in entry
    assert "⚠" not in entry


def test_format_log_entry_with_errors() -> None:
    result = BotResult(ran_at=0.0, actions_taken=0, errors=["timeout", "404"])
    entry = _format_log_entry(result, "2025-01-15T10:30:00")
    assert "⚠" in entry
    assert "2 error(s)" in entry
    assert "timeout" in entry


def test_format_log_entry_truncates_many_errors() -> None:
    result = BotResult(ran_at=0.0, errors=["e1", "e2", "e3", "e4"])
    entry = _format_log_entry(result, "2025-01-15T10:30:00")
    assert "+2 more" in entry


# ---------------------------------------------------------------------------
# _prepend_log_entry
# ---------------------------------------------------------------------------

_CONTENT_WITH_LOG = (
    "---\ntotal_runs: 1\n---\n\n# bot\n\n## Activity Log\n\n"
    "- **old entry**\n"
)

_CONTENT_NO_LOG = "---\ntotal_runs: 1\n---\n\n# bot\n"

_CONTENT_PLACEHOLDER = (
    "---\ntotal_runs: 1\n---\n\n# bot\n\n## Activity Log\n\n"
    "*No exceptional activity yet.*\n"
)


def test_prepend_log_entry_inserts_at_top() -> None:
    result = _prepend_log_entry(_CONTENT_WITH_LOG, "- new entry")
    lines = [l for l in result.split("\n") if l.startswith("- ")]
    assert lines[0] == "- new entry"
    assert lines[1] == "- **old entry**"


def test_prepend_log_entry_creates_section_when_missing() -> None:
    result = _prepend_log_entry(_CONTENT_NO_LOG, "- new entry")
    assert "## Activity Log" in result
    assert "- new entry" in result


def test_prepend_log_entry_removes_placeholder() -> None:
    result = _prepend_log_entry(_CONTENT_PLACEHOLDER, "- new entry")
    assert "*No exceptional activity yet.*" not in result
    assert "- new entry" in result


def test_prepend_log_entry_caps_at_max_entries() -> None:
    content = "## Activity Log\n\n" + "\n".join(f"- entry {i}" for i in range(25))
    result = _prepend_log_entry(content, "- newest", max_entries=5)
    entries = [l for l in result.split("\n") if l.startswith("- ")]
    assert len(entries) == 5
    assert entries[0] == "- newest"


# ---------------------------------------------------------------------------
# BaseBot._update_bot_page
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_bot_page_creates_page_when_missing() -> None:
    """_update_bot_page creates the page if it does not exist yet."""
    bot = _CountingBot()
    bot._last_ran_wall = 1_700_000_000.0
    bot.total_runs = 1
    bot.total_actions = 2

    result = BotResult(ran_at=0.0, actions_taken=2, details="did stuff")

    mock_wiki = AsyncMock()
    mock_wiki.get_page.return_value = None
    mock_wiki.create_page = AsyncMock()
    mock_wiki.__aenter__ = AsyncMock(return_value=mock_wiki)
    mock_wiki.__aexit__ = AsyncMock(return_value=None)

    with patch("factory.integrations.meshwiki_client.MeshWikiClient", return_value=mock_wiki):
        await bot._update_bot_page(result)

    mock_wiki.create_page.assert_awaited_once()
    page_name, content = mock_wiki.create_page.call_args[0]
    assert page_name == "Factory/Bots/counting"
    assert "type: bot-status" in content


@pytest.mark.asyncio
async def test_update_bot_page_patches_frontmatter_on_existing_page() -> None:
    """_update_bot_page updates frontmatter on an existing page."""
    bot = _CountingBot()
    bot._last_ran_wall = 1_700_000_000.0
    bot.total_runs = 5
    bot.total_actions = 10

    existing_content = (
        "---\ntitle: counting\ntotal_runs: 4\ntotal_actions: 8\n---\n\n# counting\n\n"
        "## Activity Log\n\n*No exceptional activity yet.*\n"
    )
    result = BotResult(ran_at=0.0, actions_taken=0, details="nothing to do")

    mock_wiki = AsyncMock()
    mock_wiki.get_page.return_value = {"content": existing_content}
    mock_wiki.create_page = AsyncMock()
    mock_wiki.__aenter__ = AsyncMock(return_value=mock_wiki)
    mock_wiki.__aexit__ = AsyncMock(return_value=None)

    with patch("factory.integrations.meshwiki_client.MeshWikiClient", return_value=mock_wiki):
        await bot._update_bot_page(result)

    _, written_content = mock_wiki.create_page.call_args[0]
    assert "total_runs: 5" in written_content
    assert "total_actions: 10" in written_content


@pytest.mark.asyncio
async def test_update_bot_page_appends_log_when_actions_taken() -> None:
    """_update_bot_page appends a log entry when actions_taken > 0."""
    bot = _CountingBot()
    bot._last_ran_wall = 1_700_000_000.0

    existing_content = (
        "---\ntitle: counting\n---\n\n# counting\n\n"
        "## Activity Log\n\n*No exceptional activity yet.*\n"
    )
    result = BotResult(ran_at=0.0, actions_taken=3, details="rescued 3")

    mock_wiki = AsyncMock()
    mock_wiki.get_page.return_value = {"content": existing_content}
    mock_wiki.create_page = AsyncMock()
    mock_wiki.__aenter__ = AsyncMock(return_value=mock_wiki)
    mock_wiki.__aexit__ = AsyncMock(return_value=None)

    with patch("factory.integrations.meshwiki_client.MeshWikiClient", return_value=mock_wiki):
        await bot._update_bot_page(result)

    _, written_content = mock_wiki.create_page.call_args[0]
    assert "✓ 3 actions" in written_content
    assert "rescued 3" in written_content


@pytest.mark.asyncio
async def test_update_bot_page_no_log_entry_when_routine() -> None:
    """_update_bot_page does NOT append a log entry for routine zero-action ticks."""
    bot = _CountingBot()
    bot._last_ran_wall = 1_700_000_000.0

    existing_content = (
        "---\ntitle: counting\n---\n\n# counting\n\n"
        "## Activity Log\n\n*No exceptional activity yet.*\n"
    )
    result = BotResult(ran_at=0.0, actions_taken=0)

    mock_wiki = AsyncMock()
    mock_wiki.get_page.return_value = {"content": existing_content}
    mock_wiki.create_page = AsyncMock()
    mock_wiki.__aenter__ = AsyncMock(return_value=mock_wiki)
    mock_wiki.__aexit__ = AsyncMock(return_value=None)

    with patch("factory.integrations.meshwiki_client.MeshWikiClient", return_value=mock_wiki):
        await bot._update_bot_page(result)

    _, written_content = mock_wiki.create_page.call_args[0]
    assert "- **" not in written_content  # no log entries added


@pytest.mark.asyncio
async def test_update_bot_page_survives_wiki_failure() -> None:
    """_update_bot_page does not raise when the wiki is unreachable."""
    bot = _CountingBot()
    bot._last_ran_wall = 1_700_000_000.0

    mock_wiki = AsyncMock()
    mock_wiki.get_page.side_effect = RuntimeError("connection refused")
    mock_wiki.__aenter__ = AsyncMock(return_value=mock_wiki)
    mock_wiki.__aexit__ = AsyncMock(return_value=None)

    with patch("factory.integrations.meshwiki_client.MeshWikiClient", return_value=mock_wiki):
        await bot._update_bot_page(BotResult(ran_at=0.0, actions_taken=1))
