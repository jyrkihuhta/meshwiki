"""Tests for the WorkerHeartbeatBot (Layer 2 of the stuck-task fix)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from factory.bots.worker_heartbeat import (
    WorkerHeartbeatBot,
    _active_graph_pages,
    _page_name_from_task_name,
)


# ---------------------------------------------------------------------------
# _page_name_from_task_name parser
# ---------------------------------------------------------------------------


def test_parse_base_task_name() -> None:
    assert _page_name_from_task_name("graph:Task_0001") == "Task_0001"


def test_parse_resume_suffix() -> None:
    assert _page_name_from_task_name("graph:Task_X:resume") == "Task_X"


def test_parse_rework_suffix() -> None:
    assert _page_name_from_task_name("graph:Task_Y:rework") == "Task_Y"


def test_parse_name_with_spaces() -> None:
    page = "Task Playbook boozt jwt 0515"
    assert _page_name_from_task_name(f"graph:{page}") == page
    assert _page_name_from_task_name(f"graph:{page}:resume") == page


def test_parse_non_graph_task_returns_none() -> None:
    assert _page_name_from_task_name("bot:bookkeeper") is None
    assert _page_name_from_task_name("uvicorn:server") is None
    assert _page_name_from_task_name("") is None


# ---------------------------------------------------------------------------
# _active_graph_pages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_active_pages_includes_running_graph_task() -> None:
    """A live graph:* task shows up in the active set; bot:* doesn't."""

    async def sleep_forever():
        await asyncio.sleep(10)

    graph_task = asyncio.create_task(sleep_forever(), name="graph:Task_A")
    bot_task = asyncio.create_task(sleep_forever(), name="bot:other")
    try:
        # Let the tasks start
        await asyncio.sleep(0)
        pages = _active_graph_pages()
        assert "Task_A" in pages
        assert "other" not in pages
    finally:
        graph_task.cancel()
        bot_task.cancel()
        for t in (graph_task, bot_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


@pytest.mark.asyncio
async def test_active_pages_dedupes_main_and_resume_variants() -> None:
    """graph:X and graph:X:resume both map to page X — only one heartbeat target."""

    async def sleep_forever():
        await asyncio.sleep(10)

    t1 = asyncio.create_task(sleep_forever(), name="graph:Task_Dup")
    t2 = asyncio.create_task(sleep_forever(), name="graph:Task_Dup:resume")
    try:
        await asyncio.sleep(0)
        pages = _active_graph_pages()
        # Should appear exactly once despite two variants
        matches = [p for p in pages if p == "Task_Dup"]
        assert len(matches) == 1
    finally:
        for t in (t1, t2):
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


# ---------------------------------------------------------------------------
# WorkerHeartbeatBot.run
# ---------------------------------------------------------------------------


def _make_bot(interval: int = 60, worker_id: str = "w-test") -> WorkerHeartbeatBot:
    with patch("factory.bots.worker_heartbeat.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(
            worker_heartbeat_interval_seconds=interval,
        )
        return WorkerHeartbeatBot(worker_id=worker_id, interval_seconds=interval)


@pytest.mark.asyncio
async def test_run_no_active_tasks_short_circuits() -> None:
    """With no graph:* tasks running, run() returns 0 actions without calling wiki."""
    bot = _make_bot()
    fake_wiki = AsyncMock()
    fake_wiki.__aenter__ = AsyncMock(return_value=fake_wiki)
    fake_wiki.__aexit__ = AsyncMock(return_value=None)

    with patch("factory.bots.worker_heartbeat.MeshWikiClient") as MWC:
        MWC.return_value = fake_wiki
        result = await bot.run()

    assert result.actions_taken == 0
    assert result.errors == []
    fake_wiki.update_metadata.assert_not_called()


@pytest.mark.asyncio
async def test_run_writes_heartbeat_for_each_active_page() -> None:
    """One update_metadata call per unique active page, with worker_id + last_heartbeat."""

    async def sleep_forever():
        await asyncio.sleep(10)

    t = asyncio.create_task(sleep_forever(), name="graph:Task_HB_R")
    try:
        await asyncio.sleep(0)

        bot = _make_bot(worker_id="w-42")
        fake_wiki = AsyncMock()
        fake_wiki.__aenter__ = AsyncMock(return_value=fake_wiki)
        fake_wiki.__aexit__ = AsyncMock(return_value=None)
        fake_wiki.update_metadata = AsyncMock()

        with patch("factory.bots.worker_heartbeat.MeshWikiClient") as MWC:
            MWC.return_value = fake_wiki
            result = await bot.run()

        assert result.actions_taken == 1
        assert result.errors == []
        call = fake_wiki.update_metadata.await_args
        assert call.args[0] == "Task_HB_R"
        fields = call.args[1]
        assert fields["worker_id"] == "w-42"
        assert "last_heartbeat" in fields
        # ISO-8601 with timezone
        assert "T" in fields["last_heartbeat"]
    finally:
        t.cancel()
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass


@pytest.mark.asyncio
async def test_run_captures_per_page_errors_continues() -> None:
    """If one page's write fails, others still proceed; error appears in result."""

    async def sleep_forever():
        await asyncio.sleep(10)

    t1 = asyncio.create_task(sleep_forever(), name="graph:Task_OK")
    t2 = asyncio.create_task(sleep_forever(), name="graph:Task_FAIL")
    try:
        await asyncio.sleep(0)

        bot = _make_bot()
        fake_wiki = AsyncMock()
        fake_wiki.__aenter__ = AsyncMock(return_value=fake_wiki)
        fake_wiki.__aexit__ = AsyncMock(return_value=None)

        async def _maybe_fail(name, fields):
            if name == "Task_FAIL":
                raise RuntimeError("page locked")

        fake_wiki.update_metadata = AsyncMock(side_effect=_maybe_fail)

        with patch("factory.bots.worker_heartbeat.MeshWikiClient") as MWC:
            MWC.return_value = fake_wiki
            result = await bot.run()

        assert result.actions_taken == 1  # Task_OK succeeded
        assert len(result.errors) == 1
        assert "Task_FAIL" in result.errors[0]
    finally:
        for t in (t1, t2):
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
