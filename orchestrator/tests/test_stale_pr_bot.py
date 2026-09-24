"""Unit tests for StalePRBot."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from factory.bots.stale_pr_bot import StalePRBot, _parse_github_ts


# ---------------------------------------------------------------------------
# _parse_github_ts
# ---------------------------------------------------------------------------


def test_parse_github_ts_valid_z():
    dt = _parse_github_ts("2024-04-24T10:30:00Z")
    assert dt is not None
    assert dt.tzinfo is not None
    assert dt.year == 2024
    assert dt.minute == 30


def test_parse_github_ts_none():
    assert _parse_github_ts(None) is None


def test_parse_github_ts_empty():
    assert _parse_github_ts("") is None


def test_parse_github_ts_invalid():
    assert _parse_github_ts("not-a-timestamp") is None


# ---------------------------------------------------------------------------
# Helpers on StalePRBot
# ---------------------------------------------------------------------------


def _make_bot() -> StalePRBot:
    return StalePRBot(interval_seconds=300)


def test_extract_failure_text_full():
    bot = _make_bot()
    check_run = {"output": {"title": "Title", "summary": "Summary", "text": "Text"}}
    result = bot._extract_failure_text(check_run)
    assert "Title" in result
    assert "Summary" in result
    assert "Text" in result


def test_extract_failure_text_empty_output():
    bot = _make_bot()
    assert bot._extract_failure_text({}) == ""


def test_extract_failure_text_truncated():
    bot = _make_bot()
    big_text = "x" * 5000
    check_run = {"output": {"text": big_text}}
    result = bot._extract_failure_text(check_run)
    assert len(result) <= 3000


def test_build_fix_page_contains_required_fields():
    bot = _make_bot()
    page = bot._build_fix_page(
        page_name="Factory/Fixes/PR-99-1",
        pr_number=99,
        pr_url="https://github.com/owner/repo/pull/99",
        branch="factory/task-0099-feature",
        original_task="Factory/Tasks/TASK-0099",
        check_name="run-tests",
        failure_age_seconds=2100.0,  # 35 minutes
        failure_text="FAILED tests/test_foo.py::test_bar",
    )
    assert "type: task" in page
    assert "assignee: factory" in page
    assert "status: planned" in page
    assert "priority: high" in page
    assert "stale_fix_pr_number: 99" in page
    assert "factory/task-0099-feature" in page
    assert "run-tests" in page
    assert "35 minutes" in page
    assert "FAILED tests/test_foo.py::test_bar" in page


def test_build_fix_page_no_failure_text():
    bot = _make_bot()
    page = bot._build_fix_page(
        "Factory/Fixes/PR-1-1", 1, "https://github.com/o/r/pull/1",
        "factory/branch", "Factory/Tasks/T1", "ci", 1800.0, ""
    )
    assert "*(no output captured)*" in page


# ---------------------------------------------------------------------------
# StalePRBot.run() — budget exhausted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_skips_when_budget_exhausted():
    bot = _make_bot()
    mock_hbr = MagicMock()
    mock_hbr.can_allocate_sandbox.return_value = False
    with patch("factory.bots.stale_pr_bot.get_hbr", return_value=mock_hbr):
        result = await bot.run()
    assert result.actions_taken == 0
    assert "daily_budget_exhausted" in result.details


# ---------------------------------------------------------------------------
# StalePRBot.run() — no open PRs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_no_open_prs():
    bot = _make_bot()
    mock_hbr = MagicMock()
    mock_hbr.can_allocate_sandbox.return_value = True

    mock_wiki = AsyncMock()
    mock_wiki.list_tasks = AsyncMock(return_value=[])
    mock_gh = AsyncMock()
    mock_gh.list_open_prs = AsyncMock(return_value=[])

    with (
        patch("factory.bots.stale_pr_bot.get_hbr", return_value=mock_hbr),
        patch("factory.bots.stale_pr_bot.MeshWikiClient") as MockWiki,
        patch("factory.bots.stale_pr_bot.GitHubClient") as MockGH,
    ):
        MockWiki.return_value.__aenter__ = AsyncMock(return_value=mock_wiki)
        MockWiki.return_value.__aexit__ = AsyncMock(return_value=False)
        MockGH.return_value.__aenter__ = AsyncMock(return_value=mock_gh)
        MockGH.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await bot.run()

    assert result.actions_taken == 0
    assert result.errors == []


# ---------------------------------------------------------------------------
# StalePRBot._process_pr — no failing checks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_pr_no_failing_checks():
    bot = _make_bot()
    pr = {"number": 1, "head": {"ref": "factory/x", "sha": "abc"}, "html_url": "https://gh/pull/1"}
    mock_gh = AsyncMock()
    mock_gh.get_check_runs = AsyncMock(return_value=[{"conclusion": "success"}])
    mock_wiki = AsyncMock()

    result = await bot._process_pr(pr, {}, mock_wiki, mock_gh)
    assert result is False
    mock_wiki.create_page.assert_not_called()


# ---------------------------------------------------------------------------
# StalePRBot._process_pr — failure too recent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_pr_failure_too_recent():
    bot = _make_bot()  # stale_pr_failure_minutes = 30
    recent_ts = (datetime.now(tz=timezone.utc) - timedelta(minutes=5)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    pr = {"number": 2, "head": {"ref": "factory/y", "sha": "def"}, "html_url": "https://gh/pull/2"}
    mock_gh = AsyncMock()
    mock_gh.get_check_runs = AsyncMock(
        return_value=[{"conclusion": "failure", "name": "tests", "completed_at": recent_ts}]
    )
    mock_wiki = AsyncMock()

    result = await bot._process_pr(pr, {}, mock_wiki, mock_gh)
    assert result is False


# ---------------------------------------------------------------------------
# StalePRBot._process_pr — no matching wiki task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_pr_no_matching_task():
    bot = _make_bot()
    old_ts = (datetime.now(tz=timezone.utc) - timedelta(hours=2)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    pr = {"number": 3, "head": {"ref": "factory/z", "sha": "fff"}, "html_url": "https://gh/pull/3"}
    mock_gh = AsyncMock()
    mock_gh.get_check_runs = AsyncMock(
        return_value=[{"conclusion": "failure", "name": "ci", "completed_at": old_ts, "output": {}}]
    )
    mock_wiki = AsyncMock()

    result = await bot._process_pr(pr, {}, mock_wiki, mock_gh)
    assert result is False
    mock_wiki.create_page.assert_not_called()


# ---------------------------------------------------------------------------
# StalePRBot._process_pr — max attempts reached
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_pr_max_attempts_reached():
    bot = _make_bot()  # max_attempts = 2
    old_ts = (datetime.now(tz=timezone.utc) - timedelta(hours=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    pr = {"number": 5, "head": {"ref": "factory/b", "sha": "aaa"}, "html_url": "https://gh/pull/5"}
    task = {"name": "Factory/Tasks/T5", "metadata": {"pr_url": "https://gh/pull/5", "stale_fix_attempts": 2}}
    pr_task_map = {5: task}

    mock_gh = AsyncMock()
    mock_gh.get_check_runs = AsyncMock(
        return_value=[{"conclusion": "failure", "name": "ci", "completed_at": old_ts, "output": {}}]
    )
    mock_wiki = AsyncMock()

    result = await bot._process_pr(pr, pr_task_map, mock_wiki, mock_gh)
    assert result is False
    mock_wiki.create_page.assert_not_called()


# ---------------------------------------------------------------------------
# StalePRBot._process_pr — idempotent (fix page already exists)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_pr_fix_page_already_exists():
    bot = _make_bot()
    old_ts = (datetime.now(tz=timezone.utc) - timedelta(hours=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    pr = {"number": 6, "head": {"ref": "factory/c", "sha": "bbb"}, "html_url": "https://gh/pull/6"}
    task = {"name": "Factory/Tasks/T6", "metadata": {"pr_url": "https://gh/pull/6", "stale_fix_attempts": 0}}
    pr_task_map = {6: task}

    mock_gh = AsyncMock()
    mock_gh.get_check_runs = AsyncMock(
        return_value=[{"conclusion": "failure", "name": "ci", "completed_at": old_ts, "output": {}}]
    )
    mock_wiki = AsyncMock()
    # The fix page already exists
    mock_wiki.get_page = AsyncMock(return_value={"name": "Factory/Fixes/PR-6-1"})

    result = await bot._process_pr(pr, pr_task_map, mock_wiki, mock_gh)
    assert result is False
    mock_wiki.create_page.assert_not_called()


# ---------------------------------------------------------------------------
# StalePRBot._process_pr — happy path: creates fix task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_pr_creates_fix_task():
    bot = _make_bot()
    old_ts = (datetime.now(tz=timezone.utc) - timedelta(hours=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    pr = {
        "number": 7,
        "head": {"ref": "factory/task-0007-add-feature", "sha": "ccc"},
        "html_url": "https://github.com/owner/repo/pull/7",
    }
    task = {
        "name": "Factory/Tasks/TASK-0007",
        "metadata": {
            "pr_url": "https://github.com/owner/repo/pull/7",
            "stale_fix_attempts": 0,
        },
    }
    pr_task_map = {7: task}

    mock_gh = AsyncMock()
    mock_gh.get_check_runs = AsyncMock(
        return_value=[
            {
                "conclusion": "failure",
                "name": "run-tests",
                "completed_at": old_ts,
                "output": {"title": "Tests failed", "summary": "4 failures", "text": ""},
            }
        ]
    )
    mock_wiki = AsyncMock()
    mock_wiki.get_page = AsyncMock(return_value=None)  # fix page doesn't exist yet
    mock_wiki.create_page = AsyncMock(return_value={})
    mock_wiki.append_to_page = AsyncMock()

    result = await bot._process_pr(pr, pr_task_map, mock_wiki, mock_gh)

    assert result is True
    mock_wiki.create_page.assert_called_once()
    created_name, created_content = mock_wiki.create_page.call_args[0]
    assert created_name == "Factory/Fixes/PR-7-1"
    assert "run-tests" in created_content
    assert "factory/task-0007-add-feature" in created_content
    assert "type: task" in created_content
    assert "status: planned" in created_content

    # Original task should be annotated
    mock_wiki.append_to_page.assert_called_once()
    call_kwargs = mock_wiki.append_to_page.call_args[1]
    assert call_kwargs["frontmatter_updates"]["stale_fix_attempts"] == 1


# ---------------------------------------------------------------------------
# StalePRBot._process_pr — annotation failure is non-fatal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_pr_annotation_failure_nonfatal():
    bot = _make_bot()
    old_ts = (datetime.now(tz=timezone.utc) - timedelta(hours=2)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    pr = {
        "number": 8,
        "head": {"ref": "factory/task-0008", "sha": "ddd"},
        "html_url": "https://github.com/owner/repo/pull/8",
    }
    task = {
        "name": "Factory/Tasks/TASK-0008",
        "metadata": {"pr_url": "https://github.com/owner/repo/pull/8", "stale_fix_attempts": 0},
    }
    pr_task_map = {8: task}

    mock_gh = AsyncMock()
    mock_gh.get_check_runs = AsyncMock(
        return_value=[{"conclusion": "failure", "name": "ci", "completed_at": old_ts, "output": {}}]
    )
    mock_wiki = AsyncMock()
    mock_wiki.get_page = AsyncMock(return_value=None)
    mock_wiki.create_page = AsyncMock(return_value={})
    mock_wiki.append_to_page = AsyncMock(side_effect=RuntimeError("wiki offline"))

    # Should still return True (fix page was created; annotation failure is non-fatal)
    result = await bot._process_pr(pr, pr_task_map, mock_wiki, mock_gh)
    assert result is True
    mock_wiki.create_page.assert_called_once()
