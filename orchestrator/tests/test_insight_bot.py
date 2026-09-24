"""Tests for the InsightBot."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from factory.bots.insight import (
    InsightBot,
    _extract_observations,
    _next_task_number,
    _parse_modified,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RECENT_ISO = (datetime.now() - timedelta(days=2)).isoformat()
_OLD_ISO = (datetime.now() - timedelta(days=10)).isoformat()


def _make_bot(interval_seconds: int = 60, model: str = "claude-haiku-4-5-20251001") -> InsightBot:
    with patch("factory.bots.insight.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(
            insight_interval_seconds=interval_seconds,
            insight_model=model,
            anthropic_api_key="test-key",
        )
        return InsightBot(interval_seconds=interval_seconds, model=model)


def _task(name: str, modified: str = _RECENT_ISO, status: str = "done") -> dict:
    return {"name": name, "metadata": {"title": name, "status": status, "modified": modified}}


def _page_with_observations(obs: str = "- git push failed") -> dict:
    return {
        "content": (
            "---\nstatus: done\n---\n\n"
            "# Task\n\n"
            "## Terminal Log\n\nsome log\n\n"
            f"## Grinder Observations\n\n{obs}\n"
        )
    }


def _page_without_observations() -> dict:
    return {"content": "---\nstatus: done\n---\n\n# Task\n\nNo observations here."}


def _make_llm_response(json_text: str) -> MagicMock:
    block = MagicMock()
    block.text = json_text
    resp = MagicMock()
    resp.content = [block]
    return resp


def _good_proposals_json() -> str:
    return (
        '[{"title": "Fix git push upstream", "slug": "fix_git_push", '
        '"description": "Grinders fail on first push.", '
        '"acceptance_criteria": "- Push succeeds first try"}]'
    )


# ---------------------------------------------------------------------------
# Unit: helpers
# ---------------------------------------------------------------------------


def test_extract_observations_returns_text() -> None:
    page = _page_with_observations("- some finding\n- another finding")
    result = _extract_observations(page["content"])
    assert result is not None
    assert "some finding" in result
    assert "another finding" in result


def test_extract_observations_missing_section_returns_none() -> None:
    assert _extract_observations("# Page\n\nNo observations.") is None


def test_extract_observations_stops_at_next_heading() -> None:
    content = (
        "## Grinder Observations\n\n- obs 1\n\n"
        "## Next Section\n\nshould not appear"
    )
    result = _extract_observations(content)
    assert result is not None
    assert "obs 1" in result
    assert "Next Section" not in result


def test_next_task_number_finds_max() -> None:
    names = ["Task_0001_foo", "Task_0005_bar", "Task_0003_baz", "Epic_0002_thing"]
    assert _next_task_number(names) == 6


def test_next_task_number_empty_list_returns_one() -> None:
    assert _next_task_number([]) == 1


def test_parse_modified_iso_string() -> None:
    result = _parse_modified("2025-01-15T10:30:00")
    assert isinstance(result, datetime)
    assert result.tzinfo is None


def test_parse_modified_strips_timezone() -> None:
    result = _parse_modified("2025-01-15T10:30:00+00:00")
    assert result is not None
    assert result.tzinfo is None


def test_parse_modified_none_returns_none() -> None:
    assert _parse_modified(None) is None


def test_parse_modified_invalid_string_returns_none() -> None:
    assert _parse_modified("not-a-date") is None


# ---------------------------------------------------------------------------
# InsightBot.run() — no observations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_returns_early_when_no_observations() -> None:
    """Bot returns 0 actions when no recently completed tasks have observations."""
    bot = _make_bot()

    mock_wiki = AsyncMock()
    mock_wiki.list_tasks.return_value = []
    mock_wiki.__aenter__ = AsyncMock(return_value=mock_wiki)
    mock_wiki.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("factory.bots.insight.MeshWikiClient", return_value=mock_wiki),
        patch("factory.bots.insight.get_settings") as mock_settings,
        patch("factory.bots.insight.anthropic.AsyncAnthropic"),
    ):
        mock_settings.return_value = MagicMock(anthropic_api_key="test-key")
        result = await bot.run()

    assert result.actions_taken == 0
    assert "no observations" in result.details


# ---------------------------------------------------------------------------
# InsightBot.run() — date filtering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_skips_tasks_older_than_7_days() -> None:
    """Bot ignores tasks whose modified timestamp is older than 7 days."""
    bot = _make_bot()

    old_task = _task("Task_0001", modified=_OLD_ISO)

    mock_wiki = AsyncMock()
    mock_wiki.list_tasks.return_value = [old_task]
    mock_wiki.__aenter__ = AsyncMock(return_value=mock_wiki)
    mock_wiki.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("factory.bots.insight.MeshWikiClient", return_value=mock_wiki),
        patch("factory.bots.insight.get_settings") as mock_settings,
        patch("factory.bots.insight.anthropic.AsyncAnthropic"),
    ):
        mock_settings.return_value = MagicMock(anthropic_api_key="test-key")
        result = await bot.run()

    assert result.actions_taken == 0
    # get_page should not have been called for the old task
    mock_wiki.get_page.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_includes_tasks_with_no_modified_date() -> None:
    """Tasks without a modified date are included (conservative — don't miss them)."""
    bot = _make_bot()

    task_no_date = {"name": "Task_0099", "metadata": {"title": "Task_0099", "modified": None}}

    mock_wiki = AsyncMock()

    async def list_tasks_side_effect(status: str) -> list:
        if status == "done":
            return [task_no_date]
        return []

    mock_wiki.list_tasks.side_effect = list_tasks_side_effect
    mock_wiki.get_page.return_value = _page_with_observations("- some obs")
    mock_wiki.__aenter__ = AsyncMock(return_value=mock_wiki)
    mock_wiki.__aexit__ = AsyncMock(return_value=None)

    mock_llm = AsyncMock()
    mock_llm.messages.create = AsyncMock(return_value=_make_llm_response("[]"))

    with (
        patch("factory.bots.insight.MeshWikiClient", return_value=mock_wiki),
        patch("factory.bots.insight.get_settings") as mock_settings,
        patch("factory.bots.insight.anthropic.AsyncAnthropic", return_value=mock_llm),
    ):
        mock_settings.return_value = MagicMock(anthropic_api_key="test-key")
        await bot.run()

    # get_page was called even without a modified date
    mock_wiki.get_page.assert_awaited_once_with("Task_0099")


# ---------------------------------------------------------------------------
# InsightBot.run() — LLM proposals and task creation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_creates_task_for_valid_proposal() -> None:
    """Bot creates a task page when LLM returns a valid proposal."""
    bot = _make_bot()

    recent_task = _task("Task_0001")

    mock_wiki = AsyncMock()

    async def list_tasks_side_effect(status: str) -> list:
        if status in ("done", "merged"):
            return [recent_task]
        return []

    mock_wiki.list_tasks.side_effect = list_tasks_side_effect
    mock_wiki.get_page.return_value = _page_with_observations("- git push failed")
    mock_wiki.create_page = AsyncMock()
    mock_wiki.__aenter__ = AsyncMock(return_value=mock_wiki)
    mock_wiki.__aexit__ = AsyncMock(return_value=None)

    mock_llm = AsyncMock()
    mock_llm.messages.create = AsyncMock(
        return_value=_make_llm_response(_good_proposals_json())
    )

    with (
        patch("factory.bots.insight.MeshWikiClient", return_value=mock_wiki),
        patch("factory.bots.insight.get_settings") as mock_settings,
        patch("factory.bots.insight.anthropic.AsyncAnthropic", return_value=mock_llm),
    ):
        mock_settings.return_value = MagicMock(anthropic_api_key="test-key")
        result = await bot.run()

    assert result.actions_taken == 1
    assert result.errors == []

    mock_wiki.create_page.assert_awaited_once()
    page_name, content = mock_wiki.create_page.call_args[0]
    assert "Task_" in page_name
    assert "fix_git_push" in page_name
    assert "status: planned" in content
    assert "assignee: factory" in content
    assert "skip_decomposition: true" in content
    assert "Fix git push upstream" in content


@pytest.mark.asyncio
async def test_run_passes_existing_titles_to_llm() -> None:
    """Existing active task titles are passed to the LLM for dedup."""
    bot = _make_bot()

    recent_task = _task("Task_0001")
    planned_task = {"name": "Task_0002", "metadata": {"title": "Fix git push issue", "status": "planned"}}

    mock_wiki = AsyncMock()

    async def list_tasks_side_effect(status: str) -> list:
        if status in ("done", "merged"):
            return [recent_task]
        if status == "planned":
            return [planned_task]
        return []

    mock_wiki.list_tasks.side_effect = list_tasks_side_effect
    mock_wiki.get_page.return_value = _page_with_observations("- obs")
    mock_wiki.create_page = AsyncMock()
    mock_wiki.__aenter__ = AsyncMock(return_value=mock_wiki)
    mock_wiki.__aexit__ = AsyncMock(return_value=None)

    mock_llm = AsyncMock()
    mock_llm.messages.create = AsyncMock(return_value=_make_llm_response("[]"))

    with (
        patch("factory.bots.insight.MeshWikiClient", return_value=mock_wiki),
        patch("factory.bots.insight.get_settings") as mock_settings,
        patch("factory.bots.insight.anthropic.AsyncAnthropic", return_value=mock_llm),
    ):
        mock_settings.return_value = MagicMock(anthropic_api_key="test-key")
        await bot.run()

    call_kwargs = mock_llm.messages.create.call_args[1]
    prompt = call_kwargs["messages"][0]["content"]
    assert "Fix git push issue" in prompt


@pytest.mark.asyncio
async def test_run_caps_proposals_at_three() -> None:
    """Bot creates at most 3 task pages per run."""
    bot = _make_bot()

    recent_task = _task("Task_0001")
    five_proposals = (
        '[{"title": "Fix A", "slug": "fix_a", "description": "Desc A", "acceptance_criteria": "- ok"},'
        '{"title": "Fix B", "slug": "fix_b", "description": "Desc B", "acceptance_criteria": "- ok"},'
        '{"title": "Fix C", "slug": "fix_c", "description": "Desc C", "acceptance_criteria": "- ok"},'
        '{"title": "Fix D", "slug": "fix_d", "description": "Desc D", "acceptance_criteria": "- ok"},'
        '{"title": "Fix E", "slug": "fix_e", "description": "Desc E", "acceptance_criteria": "- ok"}]'
    )

    mock_wiki = AsyncMock()

    async def list_tasks_side_effect(status: str) -> list:
        if status in ("done", "merged"):
            return [recent_task]
        return []

    mock_wiki.list_tasks.side_effect = list_tasks_side_effect
    mock_wiki.get_page.return_value = _page_with_observations("- obs")
    mock_wiki.create_page = AsyncMock()
    mock_wiki.__aenter__ = AsyncMock(return_value=mock_wiki)
    mock_wiki.__aexit__ = AsyncMock(return_value=None)

    mock_llm = AsyncMock()
    mock_llm.messages.create = AsyncMock(return_value=_make_llm_response(five_proposals))

    with (
        patch("factory.bots.insight.MeshWikiClient", return_value=mock_wiki),
        patch("factory.bots.insight.get_settings") as mock_settings,
        patch("factory.bots.insight.anthropic.AsyncAnthropic", return_value=mock_llm),
    ):
        mock_settings.return_value = MagicMock(anthropic_api_key="test-key")
        result = await bot.run()

    assert result.actions_taken == 3


# ---------------------------------------------------------------------------
# InsightBot.run() — LLM returns empty array
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_returns_zero_actions_when_llm_returns_empty() -> None:
    """Bot returns 0 actions when LLM finds no proposals."""
    bot = _make_bot()

    recent_task = _task("Task_0001")

    mock_wiki = AsyncMock()

    async def list_tasks_side_effect(status: str) -> list:
        if status in ("done", "merged"):
            return [recent_task]
        return []

    mock_wiki.list_tasks.side_effect = list_tasks_side_effect
    mock_wiki.get_page.return_value = _page_with_observations("- obs")
    mock_wiki.create_page = AsyncMock()
    mock_wiki.__aenter__ = AsyncMock(return_value=mock_wiki)
    mock_wiki.__aexit__ = AsyncMock(return_value=None)

    mock_llm = AsyncMock()
    mock_llm.messages.create = AsyncMock(return_value=_make_llm_response("[]"))

    with (
        patch("factory.bots.insight.MeshWikiClient", return_value=mock_wiki),
        patch("factory.bots.insight.get_settings") as mock_settings,
        patch("factory.bots.insight.anthropic.AsyncAnthropic", return_value=mock_llm),
    ):
        mock_settings.return_value = MagicMock(anthropic_api_key="test-key")
        result = await bot.run()

    assert result.actions_taken == 0
    mock_wiki.create_page.assert_not_awaited()


# ---------------------------------------------------------------------------
# InsightBot.run() — LLM failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_survives_llm_failure() -> None:
    """Bot returns 0 actions without crashing when the LLM call fails."""
    bot = _make_bot()

    recent_task = _task("Task_0001")

    mock_wiki = AsyncMock()

    async def list_tasks_side_effect(status: str) -> list:
        if status in ("done", "merged"):
            return [recent_task]
        return []

    mock_wiki.list_tasks.side_effect = list_tasks_side_effect
    mock_wiki.get_page.return_value = _page_with_observations("- obs")
    mock_wiki.create_page = AsyncMock()
    mock_wiki.__aenter__ = AsyncMock(return_value=mock_wiki)
    mock_wiki.__aexit__ = AsyncMock(return_value=None)

    mock_llm = AsyncMock()
    mock_llm.messages.create = AsyncMock(side_effect=RuntimeError("API down"))

    with (
        patch("factory.bots.insight.MeshWikiClient", return_value=mock_wiki),
        patch("factory.bots.insight.get_settings") as mock_settings,
        patch("factory.bots.insight.anthropic.AsyncAnthropic", return_value=mock_llm),
    ):
        mock_settings.return_value = MagicMock(anthropic_api_key="test-key")
        result = await bot.run()

    assert result.actions_taken == 0
    mock_wiki.create_page.assert_not_awaited()
