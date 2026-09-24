"""Tests for the BookkeeperBot reconciliation logic."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from factory.bots.bookkeeper import BookkeeperBot, _parse_updated_at

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bot(
    stale_hours: float = 2.0, heartbeat_stale_seconds: int = 300
) -> BookkeeperBot:
    """Return a BookkeeperBot configured with a 60s interval and given stale threshold."""
    with patch("factory.bots.bookkeeper.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(
            bookkeeper_interval_seconds=60,
            bookkeeper_stale_hours=stale_hours,
            worker_heartbeat_stale_seconds=heartbeat_stale_seconds,
        )
        bot = BookkeeperBot(interval_seconds=60, stale_hours=stale_hours)
    return bot


def _utc_iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _stale_task(name: str, stale_hours: float = 3.0) -> dict:
    """Return a task dict whose updated_at is stale_hours ago."""
    updated = datetime.now(tz=timezone.utc) - timedelta(hours=stale_hours)
    return {
        "name": name,
        "metadata": {"status": "in_progress", "updated_at": _utc_iso(updated)},
    }


def _stale_task_real_api_shape(name: str, stale_hours: float = 3.0) -> dict:
    """Return a task dict with the *real* MeshWiki API field name (`modified`,
    not `updated_at`). The API has always returned `modified`; the original
    bookkeeper looked for `updated_at` and silently no-op'd on every task —
    this regression test holds the line."""
    updated = datetime.now(tz=timezone.utc) - timedelta(hours=stale_hours)
    return {
        "name": name,
        "metadata": {
            "status": "in_progress",
            # Production format: ISO 8601 with microseconds, no timezone.
            "modified": updated.strftime("%Y-%m-%dT%H:%M:%S.%f"),
            "created": updated.strftime("%Y-%m-%dT%H:%M:%S.%f"),
        },
    }


def _fresh_task(name: str) -> dict:
    """Return a task dict updated 30 minutes ago (not stale)."""
    updated = datetime.now(tz=timezone.utc) - timedelta(minutes=30)
    return {
        "name": name,
        "metadata": {"status": "in_progress", "updated_at": _utc_iso(updated)},
    }


# ---------------------------------------------------------------------------
# _parse_updated_at
# ---------------------------------------------------------------------------


def test_parse_updated_at_iso_z() -> None:
    dt = _parse_updated_at("2024-01-15T10:30:00Z")
    assert dt is not None
    assert dt.tzinfo == timezone.utc
    assert dt.year == 2024
    assert dt.month == 1
    assert dt.day == 15


def test_parse_updated_at_naive() -> None:
    dt = _parse_updated_at("2024-06-01 08:00:00")
    assert dt is not None
    assert dt.tzinfo == timezone.utc


def test_parse_updated_at_none() -> None:
    assert _parse_updated_at(None) is None


def test_parse_updated_at_empty() -> None:
    assert _parse_updated_at("") is None


def test_parse_updated_at_invalid() -> None:
    assert _parse_updated_at("not-a-date") is None


# ---------------------------------------------------------------------------
# Rule 1: stuck in_progress → failed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fix_stuck_transitions_stale_task() -> None:
    """A task older than stale_hours is transitioned to failed."""
    bot = _make_bot(stale_hours=2.0)
    task = _stale_task("Task_0001", stale_hours=3.0)

    wiki = AsyncMock()
    wiki.list_tasks = AsyncMock(return_value=[task])
    wiki.transition_task = AsyncMock()
    wiki.get_page = AsyncMock(
        return_value={"name": "Task_0001", "content": "---\nstatus: in_progress\n---\n"}
    )
    wiki.create_page = AsyncMock()

    actions, errors = await bot._fix_stuck_in_progress(wiki, stale_hours=2.0)

    assert actions == 1
    assert errors == []
    wiki.transition_task.assert_awaited_once_with("Task_0001", "failed")


@pytest.mark.asyncio
async def test_fix_stuck_skips_fresh_task() -> None:
    """A task updated recently is not transitioned."""
    bot = _make_bot(stale_hours=2.0)
    task = _fresh_task("Task_0001")

    wiki = AsyncMock()
    wiki.list_tasks = AsyncMock(return_value=[task])
    wiki.transition_task = AsyncMock()

    actions, errors = await bot._fix_stuck_in_progress(wiki, stale_hours=2.0)

    assert actions == 0
    assert errors == []
    wiki.transition_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_fix_stuck_skips_task_without_updated_at() -> None:
    """A task with no updated_at is skipped (cannot determine age)."""
    bot = _make_bot(stale_hours=2.0)
    task = {"name": "Task_0002", "metadata": {"status": "in_progress"}}

    wiki = AsyncMock()
    wiki.list_tasks = AsyncMock(return_value=[task])
    wiki.transition_task = AsyncMock()

    actions, errors = await bot._fix_stuck_in_progress(wiki, stale_hours=2.0)

    assert actions == 0
    assert errors == []
    wiki.transition_task.assert_not_awaited()


# ---------------------------------------------------------------------------
# Regression: real MeshWiki API field name is `modified`, not `updated_at`.
# The original bookkeeper read `updated_at` exclusively, so production
# never had a parseable timestamp and never timed out a single task.
# Result: scheduler cap (3/3) filled with zombies for 2.5 weeks (Apr 20
# → May 5 2026), blocking the entire Factory queue.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fix_stuck_reads_modified_field_from_real_api() -> None:
    """Real MeshWiki responses use `modified`. Bookkeeper must read it."""
    bot = _make_bot(stale_hours=2.0)
    task = _stale_task_real_api_shape("Task_real", stale_hours=3.0)

    wiki = AsyncMock()
    wiki.list_tasks = AsyncMock(return_value=[task])
    wiki.transition_task = AsyncMock()
    wiki.get_page = AsyncMock(
        return_value={"name": "Task_real", "content": "---\nstatus: in_progress\n---\n"}
    )
    wiki.create_page = AsyncMock()

    actions, errors = await bot._fix_stuck_in_progress(wiki, stale_hours=2.0)

    assert actions == 1, f"expected 1 transition, got {actions}; errors={errors}"
    assert errors == []
    wiki.transition_task.assert_awaited_once_with("Task_real", "failed")


@pytest.mark.asyncio
async def test_fix_stuck_real_api_fresh_task_skipped() -> None:
    """Real-shape (modified) timestamp from 30min ago is fresh, not stale."""
    bot = _make_bot(stale_hours=2.0)
    task = _stale_task_real_api_shape("Task_fresh", stale_hours=0.5)  # 30min

    wiki = AsyncMock()
    wiki.list_tasks = AsyncMock(return_value=[task])
    wiki.transition_task = AsyncMock()

    actions, errors = await bot._fix_stuck_in_progress(wiki, stale_hours=2.0)

    assert actions == 0
    wiki.transition_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_fix_stuck_prefers_modified_over_updated_at() -> None:
    """If both fields present (transitional state), `modified` wins.
    Defends against a stale `updated_at` masking a fresh `modified`."""
    bot = _make_bot(stale_hours=2.0)
    fresh = datetime.now(tz=timezone.utc) - timedelta(minutes=15)
    very_old = datetime.now(tz=timezone.utc) - timedelta(hours=10)
    task = {
        "name": "Task_mixed",
        "metadata": {
            "status": "in_progress",
            "modified": fresh.strftime("%Y-%m-%dT%H:%M:%S.%f"),
            "updated_at": _utc_iso(very_old),
        },
    }

    wiki = AsyncMock()
    wiki.list_tasks = AsyncMock(return_value=[task])
    wiki.transition_task = AsyncMock()

    actions, errors = await bot._fix_stuck_in_progress(wiki, stale_hours=2.0)

    assert actions == 0, "fresh `modified` must win over stale `updated_at`"
    wiki.transition_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_fix_stuck_list_tasks_error() -> None:
    """When list_tasks raises the error is captured and 0 actions taken."""
    bot = _make_bot()
    wiki = AsyncMock()
    wiki.list_tasks = AsyncMock(side_effect=RuntimeError("timeout"))

    actions, errors = await bot._fix_stuck_in_progress(wiki, stale_hours=2.0)

    assert actions == 0
    assert len(errors) == 1
    assert "list_tasks" in errors[0]


@pytest.mark.asyncio
async def test_fix_stuck_transition_error_captured() -> None:
    """A transition failure is captured as an error without aborting other tasks."""
    bot = _make_bot(stale_hours=2.0)
    stale1 = _stale_task("Task_0001", stale_hours=3.0)
    stale2 = _stale_task("Task_0002", stale_hours=4.0)

    wiki = AsyncMock()
    wiki.list_tasks = AsyncMock(return_value=[stale1, stale2])
    wiki.transition_task = AsyncMock(side_effect=[RuntimeError("db error"), None])
    wiki.get_page = AsyncMock(
        return_value={"name": "Task_0002", "content": "---\nstatus: in_progress\n---\n"}
    )
    wiki.create_page = AsyncMock()

    actions, errors = await bot._fix_stuck_in_progress(wiki, stale_hours=2.0)

    # Task_0001 failed; Task_0002 succeeded
    assert actions == 1
    assert len(errors) == 1
    assert "Task_0001" in errors[0]


@pytest.mark.asyncio
async def test_fix_stuck_appends_agent_log() -> None:
    """The agent log note is appended to the page after transition."""
    bot = _make_bot(stale_hours=2.0)
    task = _stale_task("Task_0001", stale_hours=3.0)
    content = "---\nstatus: in_progress\n---\n\n# Body\n\n## Agent Log\n- prev entry"

    wiki = AsyncMock()
    wiki.list_tasks = AsyncMock(return_value=[task])
    wiki.transition_task = AsyncMock()
    wiki.get_page = AsyncMock(return_value={"name": "Task_0001", "content": content})
    wiki.create_page = AsyncMock()

    await bot._fix_stuck_in_progress(wiki, stale_hours=2.0)

    wiki.create_page.assert_awaited_once()
    saved_content: str = wiki.create_page.call_args[0][1]
    assert "Bookkeeper" in saved_content
    assert "failed" in saved_content


# ---------------------------------------------------------------------------
# Rule 2: merged PRs → merged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fix_merged_prs_transitions_merged_pr() -> None:
    """A task in in_review whose PR is merged is transitioned to merged."""
    bot = _make_bot()
    task = {
        "name": "Task_0003",
        "metadata": {
            "status": "in_review",
            "pr_url": "https://github.com/owner/repo/pull/42",
        },
    }

    wiki = AsyncMock()
    wiki.list_tasks = AsyncMock(return_value=[task])
    wiki.transition_task = AsyncMock()
    wiki.get_page = AsyncMock(
        return_value={"name": "Task_0003", "content": "---\nstatus: in_review\n---\n"}
    )
    wiki.create_page = AsyncMock()

    github = AsyncMock()
    github.get_pr = AsyncMock(
        return_value={"number": 42, "merged": True, "state": "closed"}
    )

    actions, errors = await bot._fix_merged_prs(wiki, github)

    assert actions == 1
    assert errors == []
    wiki.transition_task.assert_awaited_once_with("Task_0003", "merged")


@pytest.mark.asyncio
async def test_fix_merged_prs_skips_open_pr() -> None:
    """A task whose PR is still open is not transitioned."""
    bot = _make_bot()
    task = {
        "name": "Task_0004",
        "metadata": {
            "status": "in_review",
            "pr_url": "https://github.com/owner/repo/pull/7",
        },
    }

    wiki = AsyncMock()
    wiki.list_tasks = AsyncMock(return_value=[task])
    wiki.transition_task = AsyncMock()

    github = AsyncMock()
    github.get_pr = AsyncMock(
        return_value={"number": 7, "merged": False, "state": "open"}
    )

    actions, errors = await bot._fix_merged_prs(wiki, github)

    assert actions == 0
    assert errors == []
    wiki.transition_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_fix_merged_prs_skips_task_without_pr_url() -> None:
    """A task with no pr_url frontmatter is skipped."""
    bot = _make_bot()
    task = {"name": "Task_0005", "metadata": {"status": "in_review"}}

    wiki = AsyncMock()
    wiki.list_tasks = AsyncMock(return_value=[task])
    wiki.transition_task = AsyncMock()

    github = AsyncMock()
    github.get_pr = AsyncMock()

    actions, errors = await bot._fix_merged_prs(wiki, github)

    assert actions == 0
    github.get_pr.assert_not_awaited()


@pytest.mark.asyncio
async def test_fix_merged_prs_list_tasks_error() -> None:
    """When list_tasks raises the error is captured."""
    bot = _make_bot()
    wiki = AsyncMock()
    wiki.list_tasks = AsyncMock(side_effect=ConnectionError("network"))

    github = AsyncMock()

    actions, errors = await bot._fix_merged_prs(wiki, github)

    assert actions == 0
    assert len(errors) == 1
    assert "list_tasks" in errors[0]


@pytest.mark.asyncio
async def test_fix_merged_prs_github_error_captured() -> None:
    """GitHub API errors are captured without aborting other tasks."""
    bot = _make_bot()
    task1 = {
        "name": "Task_0006",
        "metadata": {
            "status": "in_review",
            "pr_url": "https://github.com/owner/repo/pull/10",
        },
    }
    task2 = {
        "name": "Task_0007",
        "metadata": {
            "status": "in_review",
            "pr_url": "https://github.com/owner/repo/pull/11",
        },
    }

    wiki = AsyncMock()
    wiki.list_tasks = AsyncMock(return_value=[task1, task2])
    wiki.transition_task = AsyncMock()
    wiki.get_page = AsyncMock(
        return_value={"name": "Task_0007", "content": "---\nstatus: in_review\n---\n"}
    )
    wiki.create_page = AsyncMock()

    github = AsyncMock()
    github.get_pr = AsyncMock(
        side_effect=[
            httpx.HTTPStatusError(
                "404", request=MagicMock(), response=MagicMock(status_code=404)
            ),
            {"number": 11, "merged": True, "state": "closed"},
        ]
    )

    actions, errors = await bot._fix_merged_prs(wiki, github)

    # task1 failed (GitHub error), task2 succeeded
    assert actions == 1
    assert len(errors) == 1
    assert "Task_0006" in errors[0]


@pytest.mark.asyncio
async def test_fix_merged_prs_invalid_pr_url() -> None:
    """A pr_url from which no PR number can be extracted is skipped."""
    bot = _make_bot()
    task = {
        "name": "Task_0008",
        "metadata": {
            "status": "in_review",
            "pr_url": "https://github.com/owner/repo/issues/5",  # issues, not pulls
        },
    }

    wiki = AsyncMock()
    wiki.list_tasks = AsyncMock(return_value=[task])

    github = AsyncMock()
    github.get_pr = AsyncMock()

    actions, errors = await bot._fix_merged_prs(wiki, github)

    assert actions == 0
    github.get_pr.assert_not_awaited()


# ---------------------------------------------------------------------------
# Full run() integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_combines_both_rules() -> None:
    """run() executes both rules and aggregates totals."""
    bot = _make_bot(stale_hours=2.0)
    stale = _stale_task("Task_0010", stale_hours=3.0)
    in_review = {
        "name": "Task_0011",
        "metadata": {
            "status": "in_review",
            "pr_url": "https://github.com/owner/repo/pull/99",
        },
    }

    wiki_mock = AsyncMock()
    github_mock = AsyncMock()

    def list_tasks_side_effect(status=None):
        if status == "in_progress":
            return [stale]
        if status == "in_review":
            return [in_review]
        return []

    wiki_mock.list_tasks = AsyncMock(side_effect=list_tasks_side_effect)
    wiki_mock.transition_task = AsyncMock()
    wiki_mock.get_page = AsyncMock(
        return_value={"name": "Task_0010", "content": "---\nstatus: in_progress\n---\n"}
    )
    wiki_mock.create_page = AsyncMock()
    github_mock.get_pr = AsyncMock(
        return_value={"number": 99, "merged": True, "state": "closed"}
    )

    with (
        patch(
            "factory.bots.bookkeeper.MeshWikiClient",
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=wiki_mock), __aexit__=AsyncMock()
            ),
        ),
        patch(
            "factory.bots.bookkeeper.GitHubClient",
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=github_mock), __aexit__=AsyncMock()
            ),
        ),
    ):
        result = await bot.run()

    assert result.actions_taken == 2
    assert result.errors == []


@pytest.mark.asyncio
async def test_run_returns_bot_result() -> None:
    """run() always returns a BotResult even when nothing to do."""
    bot = _make_bot()

    wiki_mock = AsyncMock()
    wiki_mock.list_tasks = AsyncMock(return_value=[])
    github_mock = AsyncMock()

    with (
        patch(
            "factory.bots.bookkeeper.MeshWikiClient",
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=wiki_mock), __aexit__=AsyncMock()
            ),
        ),
        patch(
            "factory.bots.bookkeeper.GitHubClient",
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=github_mock), __aexit__=AsyncMock()
            ),
        ),
    ):
        result = await bot.run()

    assert result.actions_taken == 0
    assert result.errors == []
    assert result.ran_at >= 0


# ---------------------------------------------------------------------------
# Rule 1b: stale heartbeat → failed
# ---------------------------------------------------------------------------


def _task_with_heartbeat(name: str, age_seconds: int, worker_id: str = "w-1") -> dict:
    """Return an in_progress task whose last_heartbeat is age_seconds ago."""
    hb = datetime.now(tz=timezone.utc) - timedelta(seconds=age_seconds)
    return {
        "name": name,
        "metadata": {
            "status": "in_progress",
            "modified": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f"),
            "last_heartbeat": hb.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "worker_id": worker_id,
        },
    }


@pytest.mark.asyncio
async def test_fix_stale_heartbeats_transitions_stale_task() -> None:
    """A task whose last_heartbeat is older than threshold is transitioned to failed."""
    bot = _make_bot(heartbeat_stale_seconds=300)
    # 10 min stale heartbeat (modified is fresh, so Rule 1 wouldn't catch it)
    task = _task_with_heartbeat("Task_HB1", age_seconds=600)

    wiki = AsyncMock()
    wiki.list_tasks = AsyncMock(return_value=[task])
    wiki.transition_task = AsyncMock()
    wiki.get_page = AsyncMock(
        return_value={"name": "Task_HB1", "content": "---\nstatus: in_progress\n---\n"}
    )
    wiki.create_page = AsyncMock()

    actions, errors = await bot._fix_stale_heartbeats(wiki, stale_seconds=300)

    assert actions == 1
    assert errors == []
    wiki.transition_task.assert_awaited_once_with("Task_HB1", "failed")


@pytest.mark.asyncio
async def test_fix_stale_heartbeats_skips_fresh_task() -> None:
    """A task with a recent heartbeat is not transitioned."""
    bot = _make_bot(heartbeat_stale_seconds=300)
    task = _task_with_heartbeat("Task_HB2", age_seconds=60)  # 1 min ago

    wiki = AsyncMock()
    wiki.list_tasks = AsyncMock(return_value=[task])
    wiki.transition_task = AsyncMock()

    actions, errors = await bot._fix_stale_heartbeats(wiki, stale_seconds=300)

    assert actions == 0
    assert errors == []
    wiki.transition_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_fix_stale_heartbeats_skips_task_without_heartbeat() -> None:
    """A task with no last_heartbeat is left for Rule 1 to handle."""
    bot = _make_bot(heartbeat_stale_seconds=300)
    task = {
        "name": "Task_NoHB",
        "metadata": {"status": "in_progress"},  # no last_heartbeat
    }

    wiki = AsyncMock()
    wiki.list_tasks = AsyncMock(return_value=[task])
    wiki.transition_task = AsyncMock()

    actions, errors = await bot._fix_stale_heartbeats(wiki, stale_seconds=300)

    assert actions == 0
    assert errors == []


@pytest.mark.asyncio
async def test_fix_stale_heartbeats_list_tasks_error() -> None:
    """list_tasks failure is captured in errors, not raised."""
    bot = _make_bot(heartbeat_stale_seconds=300)
    wiki = AsyncMock()
    wiki.list_tasks = AsyncMock(side_effect=httpx.HTTPError("boom"))

    actions, errors = await bot._fix_stale_heartbeats(wiki, stale_seconds=300)

    assert actions == 0
    assert len(errors) == 1
    assert "boom" in errors[0]


@pytest.mark.asyncio
async def test_fix_stale_heartbeats_transition_error_captured() -> None:
    """If transition_task raises, the error is captured and the loop continues."""
    bot = _make_bot(heartbeat_stale_seconds=300)
    t1 = _task_with_heartbeat("Task_X", age_seconds=600)
    t2 = _task_with_heartbeat("Task_Y", age_seconds=600)

    wiki = AsyncMock()
    wiki.list_tasks = AsyncMock(return_value=[t1, t2])

    async def _flaky_transition(name: str, status: str) -> None:
        if name == "Task_X":
            raise httpx.HTTPError("422")

    wiki.transition_task = AsyncMock(side_effect=_flaky_transition)
    wiki.get_page = AsyncMock(
        return_value={"name": "_", "content": "---\nstatus: in_progress\n---\n"}
    )
    wiki.create_page = AsyncMock()

    actions, errors = await bot._fix_stale_heartbeats(wiki, stale_seconds=300)

    assert actions == 1  # Task_Y succeeded
    assert len(errors) == 1
    assert "Task_X" in errors[0]
