"""Tests for SchedulerBot — the autonomous backlog dispatcher."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from factory.bots.scheduler import SchedulerBot, _priority_rank
from factory.integrations.minimax_client import TokenPlanStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _task(
    name: str,
    status: str = "planned",
    assignee: str = "factory",
    priority: str = "normal",
    parent_task: str | None = None,
    modified: str = "2026-01-01T00:00:00",
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "status": status,
        "assignee": assignee,
        "priority": priority,
        "modified": modified,
    }
    if parent_task:
        metadata["parent_task"] = parent_task
    return {"name": name, "metadata": metadata}


class TestPriorityRank:
    def test_urgent_is_highest(self) -> None:
        assert _priority_rank(_task("t", priority="urgent")) == 4

    def test_high(self) -> None:
        assert _priority_rank(_task("t", priority="high")) == 3

    def test_normal(self) -> None:
        assert _priority_rank(_task("t", priority="normal")) == 2

    def test_low(self) -> None:
        assert _priority_rank(_task("t", priority="low")) == 1

    def test_unknown_falls_back_to_normal(self) -> None:
        assert _priority_rank(_task("t", priority="blocker")) == 2

    def test_missing_priority_is_normal(self) -> None:
        assert _priority_rank({"name": "t", "metadata": {}}) == 2


# ---------------------------------------------------------------------------
# SchedulerBot.run() tests
# ---------------------------------------------------------------------------


def _make_bot(
    cap: int = 3,
    threshold: int = 0,
    interval: int = 60,
) -> SchedulerBot:
    bot = SchedulerBot(interval_seconds=interval)
    bot._settings = MagicMock(
        max_concurrent_parent_tasks=cap,
        minimax_token_threshold=threshold,
        minimax_api_key="test-key",
    )
    return bot


class TestSchedulerBotGlobalCap:
    @pytest.mark.asyncio
    async def test_does_not_dispatch_when_at_cap(self) -> None:
        bot = _make_bot(cap=2)
        in_progress = [
            _task("A", status="in_progress"),
            _task("B", status="in_progress"),
        ]

        with patch("factory.bots.scheduler.MeshWikiClient") as MockWiki:
            wiki_inst = MockWiki.return_value.__aenter__.return_value
            wiki_inst.list_tasks = AsyncMock(
                side_effect=lambda **kw: (
                    in_progress if kw.get("status") == "in_progress" else []
                )
            )
            result = await bot.run()

        assert result.actions_taken == 0
        assert "at cap" in result.details

    @pytest.mark.asyncio
    async def test_dispatches_when_below_cap(self) -> None:
        bot = _make_bot(cap=3)
        in_progress = [_task("A", status="in_progress")]
        planned = [
            _task("P1", priority="high"),
            _task("P2", priority="normal"),
            _task("P3", priority="low"),
        ]

        with patch("factory.bots.scheduler.MeshWikiClient") as MockWiki:
            wiki_inst = MockWiki.return_value.__aenter__.return_value

            async def _list_tasks(**kw):
                if kw.get("status") == "in_progress":
                    return in_progress
                return planned

            wiki_inst.list_tasks = AsyncMock(side_effect=_list_tasks)
            wiki_inst.transition_task = AsyncMock()

            result = await bot.run()

        # cap=3, active=1 → 2 slots; 3 planned tasks but only 2 slots
        assert result.actions_taken == 2

    @pytest.mark.asyncio
    async def test_skips_subtask_pages_in_active_count(self) -> None:
        bot = _make_bot(cap=2)
        # A subtask (has parent_task) should not count towards the cap
        in_progress = [
            _task("Parent", status="in_progress"),
            _task("Sub", status="in_progress", parent_task="Parent"),
        ]
        planned = [_task("New")]

        with patch("factory.bots.scheduler.MeshWikiClient") as MockWiki:
            wiki_inst = MockWiki.return_value.__aenter__.return_value

            async def _list_tasks(**kw):
                if kw.get("status") == "in_progress":
                    return in_progress
                return planned

            wiki_inst.list_tasks = AsyncMock(side_effect=_list_tasks)
            wiki_inst.transition_task = AsyncMock()

            result = await bot.run()

        # Only 1 parent in-progress, cap=2 → 1 slot available → dispatches 1
        assert result.actions_taken == 1


class TestSchedulerBotPrioritySort:
    @pytest.mark.asyncio
    async def test_dispatches_highest_priority_first(self) -> None:
        bot = _make_bot(cap=2)  # 1 slot free (active=1)
        dispatched_names: list[str] = []

        planned = [
            _task("low-task", priority="low", modified="2026-01-01"),
            _task("urgent-task", priority="urgent", modified="2026-01-03"),
            _task("normal-task", priority="normal", modified="2026-01-02"),
        ]

        with patch("factory.bots.scheduler.MeshWikiClient") as MockWiki:
            wiki_inst = MockWiki.return_value.__aenter__.return_value

            async def _list_tasks(**kw):
                if kw.get("status") == "in_progress":
                    return [_task("A", status="in_progress")]
                return planned

            async def _transition(name, status):
                dispatched_names.append(name)

            wiki_inst.list_tasks = AsyncMock(side_effect=_list_tasks)
            wiki_inst.transition_task = AsyncMock(side_effect=_transition)

            await bot.run()

        # cap=2, active=1 → 1 slot → dispatches only 1, must be urgent
        assert dispatched_names == ["urgent-task"]

    @pytest.mark.asyncio
    async def test_tiebreaks_by_age(self) -> None:
        bot = _make_bot(cap=2)
        dispatched_names: list[str] = []

        planned = [
            _task("newer", priority="high", modified="2026-04-01"),
            _task("older", priority="high", modified="2026-01-01"),
        ]

        with patch("factory.bots.scheduler.MeshWikiClient") as MockWiki:
            wiki_inst = MockWiki.return_value.__aenter__.return_value

            async def _list_tasks(**kw):
                if kw.get("status") == "in_progress":
                    return [_task("A", status="in_progress")]
                return planned

            async def _transition(name, status):
                dispatched_names.append(name)

            wiki_inst.list_tasks = AsyncMock(side_effect=_list_tasks)
            wiki_inst.transition_task = AsyncMock(side_effect=_transition)

            await bot.run()

        # Same priority → oldest modified first
        assert dispatched_names == ["older"]


class TestSchedulerBotMiniMaxQuota:
    @pytest.mark.asyncio
    async def test_pauses_when_quota_below_threshold(self) -> None:
        bot = _make_bot(cap=3, threshold=1_000_000)

        low_quota = TokenPlanStatus(remaining=500_000, limit=10_000_000, reset_at=None)

        with (
            patch("factory.bots.scheduler.MeshWikiClient") as MockWiki,
            patch("factory.bots.scheduler.MiniMaxUsageClient") as MockMM,
        ):
            wiki_inst = MockWiki.return_value.__aenter__.return_value
            wiki_inst.list_tasks = AsyncMock(return_value=[])
            mm_inst = MockMM.return_value.__aenter__.return_value
            mm_inst.get_token_plan_remaining = AsyncMock(return_value=low_quota)

            result = await bot.run()

        assert result.actions_taken == 0
        assert "quota low" in result.details

    @pytest.mark.asyncio
    async def test_allows_dispatch_when_quota_above_threshold(self) -> None:
        bot = _make_bot(cap=3, threshold=1_000_000)

        high_quota = TokenPlanStatus(
            remaining=9_000_000, limit=10_000_000, reset_at=None
        )
        planned = [_task("T1")]

        with (
            patch("factory.bots.scheduler.MeshWikiClient") as MockWiki,
            patch("factory.bots.scheduler.MiniMaxUsageClient") as MockMM,
        ):
            wiki_inst = MockWiki.return_value.__aenter__.return_value

            async def _list_tasks(**kw):
                if kw.get("status") == "in_progress":
                    return []
                return planned

            wiki_inst.list_tasks = AsyncMock(side_effect=_list_tasks)
            wiki_inst.transition_task = AsyncMock()
            mm_inst = MockMM.return_value.__aenter__.return_value
            mm_inst.get_token_plan_remaining = AsyncMock(return_value=high_quota)

            result = await bot.run()

        assert result.actions_taken == 1

    @pytest.mark.asyncio
    async def test_allows_dispatch_when_quota_unknown(self) -> None:
        """Unknown quota (None) must not block dispatch."""
        bot = _make_bot(cap=3, threshold=1_000_000)

        unknown_quota = TokenPlanStatus(remaining=None, limit=None, reset_at=None)
        planned = [_task("T1")]

        with (
            patch("factory.bots.scheduler.MeshWikiClient") as MockWiki,
            patch("factory.bots.scheduler.MiniMaxUsageClient") as MockMM,
        ):
            wiki_inst = MockWiki.return_value.__aenter__.return_value

            async def _list_tasks(**kw):
                if kw.get("status") == "in_progress":
                    return []
                return planned

            wiki_inst.list_tasks = AsyncMock(side_effect=_list_tasks)
            wiki_inst.transition_task = AsyncMock()
            mm_inst = MockMM.return_value.__aenter__.return_value
            mm_inst.get_token_plan_remaining = AsyncMock(return_value=unknown_quota)

            result = await bot.run()

        assert result.actions_taken == 1

    @pytest.mark.asyncio
    async def test_threshold_zero_skips_quota_check(self) -> None:
        """FACTORY_MINIMAX_TOKEN_THRESHOLD=0 should never call the usage API."""
        bot = _make_bot(cap=3, threshold=0)
        planned = [_task("T1")]

        with (
            patch("factory.bots.scheduler.MeshWikiClient") as MockWiki,
            patch("factory.bots.scheduler.MiniMaxUsageClient") as MockMM,
        ):
            wiki_inst = MockWiki.return_value.__aenter__.return_value

            async def _list_tasks(**kw):
                if kw.get("status") == "in_progress":
                    return []
                return planned

            wiki_inst.list_tasks = AsyncMock(side_effect=_list_tasks)
            wiki_inst.transition_task = AsyncMock()

            result = await bot.run()

        # MiniMaxUsageClient should never have been entered
        MockMM.assert_not_called()
        assert result.actions_taken == 1


class TestSchedulerBotSubtaskSkip:
    @pytest.mark.asyncio
    async def test_skips_subtask_pages_in_planned(self) -> None:
        bot = _make_bot(cap=3)
        planned = [
            _task("Real_Task"),
            _task("Subtask", parent_task="Real_Task"),
        ]

        with patch("factory.bots.scheduler.MeshWikiClient") as MockWiki:
            wiki_inst = MockWiki.return_value.__aenter__.return_value

            async def _list_tasks(**kw):
                if kw.get("status") == "in_progress":
                    return []
                return planned

            wiki_inst.list_tasks = AsyncMock(side_effect=_list_tasks)
            wiki_inst.transition_task = AsyncMock()

            result = await bot.run()

        # Only the non-subtask should be dispatched
        assert result.actions_taken == 1
        call_args = wiki_inst.transition_task.call_args_list
        dispatched = [c.args[0] for c in call_args]
        assert "Subtask" not in dispatched
        assert "Real_Task" in dispatched
