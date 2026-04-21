"""Unit tests for the CIFixerBot."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from factory.bots.ci_fixer import CIFixerBot, _job_id_from_details_url, _parse_analysis


# ---------------------------------------------------------------------------
# _parse_analysis
# ---------------------------------------------------------------------------


def test_parse_analysis_all_fields():
    text = (
        "CATEGORY: import_error\n"
        "RETRYABLE: yes\n"
        "ROOT_CAUSE: bs4 not installed\n"
        "SUGGESTED_FIX: Add beautifulsoup4 to dev deps\n"
    )
    result = _parse_analysis(text)
    assert result["category"] == "import_error"
    assert result["retryable"] == "yes"
    assert result["root_cause"] == "bs4 not installed"
    assert result["suggested_fix"] == "Add beautifulsoup4 to dev deps"


def test_parse_analysis_partial():
    text = "CATEGORY: lint_error\nRETRYABLE: no\n"
    result = _parse_analysis(text)
    assert result["category"] == "lint_error"
    assert result.get("root_cause") is None


def test_parse_analysis_empty():
    assert _parse_analysis("") == {}


# ---------------------------------------------------------------------------
# _job_id_from_details_url
# ---------------------------------------------------------------------------


def test_job_id_from_details_url_valid():
    url = "https://github.com/owner/repo/actions/runs/12345/jobs/67890"
    assert _job_id_from_details_url(url) == 67890


def test_job_id_from_details_url_singular():
    url = "https://github.com/owner/repo/actions/runs/12345/job/67890"
    assert _job_id_from_details_url(url) == 67890


def test_job_id_from_details_url_no_match():
    assert _job_id_from_details_url("https://example.com/no-job-here") is None


# ---------------------------------------------------------------------------
# CIFixerBot._find_candidates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_candidates_skips_no_pr_url():
    bot = CIFixerBot(interval_seconds=120)
    wiki = AsyncMock()
    wiki.list_tasks = AsyncMock(
        return_value=[{"name": "TaskNoPR", "metadata": {"ci_fix_attempts": 0}}]
    )
    errors: list = []
    result = await bot._find_candidates(wiki, errors)
    assert result == []


@pytest.mark.asyncio
async def test_find_candidates_skips_max_attempts():
    bot = CIFixerBot(interval_seconds=120)
    wiki = AsyncMock()
    wiki.list_tasks = AsyncMock(
        return_value=[
            {
                "name": "TaskAtCap",
                "metadata": {
                    "pr_url": "https://github.com/owner/repo/pull/42",
                    "ci_fix_attempts": 2,
                },
            }
        ]
    )
    errors: list = []
    result = await bot._find_candidates(wiki, errors)
    assert result == []


@pytest.mark.asyncio
async def test_find_candidates_returns_valid_entry():
    bot = CIFixerBot(interval_seconds=120)
    wiki = AsyncMock()
    wiki.list_tasks = AsyncMock(
        return_value=[
            {
                "name": "TaskOK",
                "metadata": {
                    "pr_url": "https://github.com/owner/repo/pull/7",
                    "ci_fix_attempts": 1,
                },
            }
        ]
    )
    errors: list = []
    result = await bot._find_candidates(wiki, errors)
    assert len(result) == 1
    assert result[0]["task_name"] == "TaskOK"
    assert result[0]["pr_number"] == 7
    assert result[0]["attempts"] == 1


# ---------------------------------------------------------------------------
# CIFixerBot._process — no failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_skips_when_no_failed_checks():
    bot = CIFixerBot(interval_seconds=120)
    wiki = AsyncMock()
    gh = AsyncMock()
    gh.get_pr = AsyncMock(return_value={"head": {"sha": "abc123"}})
    gh.get_check_runs = AsyncMock(
        return_value=[{"conclusion": "success", "name": "tests", "output": {}}]
    )
    anthropic_client = AsyncMock()

    item = {"task_name": "MyTask", "pr_number": 1, "attempts": 0}
    acted = await bot._process(item, wiki, gh, anthropic_client)
    assert acted is False
    anthropic_client.messages.create.assert_not_called()


# ---------------------------------------------------------------------------
# CIFixerBot._process — with failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_posts_comment_and_annotates_wiki():
    bot = CIFixerBot(interval_seconds=120)
    wiki = AsyncMock()
    wiki.append_to_page = AsyncMock()
    gh = AsyncMock()
    gh.get_pr = AsyncMock(return_value={"head": {"sha": "deadbeef"}})
    gh.get_check_runs = AsyncMock(
        return_value=[
            {
                "conclusion": "failure",
                "name": "Python tests",
                "details_url": "",
                "output": {
                    "title": "1 error",
                    "summary": "ModuleNotFoundError: No module named 'bs4'",
                    "text": "",
                },
            }
        ]
    )
    gh.create_pr_comment = AsyncMock()

    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text=(
        "CATEGORY: import_error\n"
        "RETRYABLE: yes\n"
        "ROOT_CAUSE: bs4 not installed\n"
        "SUGGESTED_FIX: Add beautifulsoup4 to pyproject.toml dev deps\n"
    ))]
    anthropic_client = AsyncMock()
    anthropic_client.messages.create = AsyncMock(return_value=mock_resp)

    item = {"task_name": "MyTask", "pr_number": 5, "attempts": 0}
    acted = await bot._process(item, wiki, gh, anthropic_client)

    assert acted is True
    gh.create_pr_comment.assert_awaited_once()
    comment_body = gh.create_pr_comment.call_args[0][1]
    assert "import_error" in comment_body
    assert "bs4" in comment_body

    wiki.append_to_page.assert_awaited_once()
    kwargs = wiki.append_to_page.call_args
    assert kwargs[1]["frontmatter_updates"]["ci_fix_attempts"] == 1
