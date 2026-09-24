"""Tests for the TerminalReviewBot."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from factory.bots.terminal_review import (
    _OBSERVATIONS_MARKER,
    _TERMINAL_LOG_MARKER,
    TerminalReviewBot,
    _extract_terminal_log,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bot(
    interval_seconds: int = 60,
    batch_size: int = 5,
    model: str = "claude-haiku-4-5-20251001",
) -> TerminalReviewBot:
    """Return a TerminalReviewBot with test-friendly defaults."""
    with patch("factory.bots.terminal_review.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(
            terminal_review_interval_seconds=interval_seconds,
            terminal_review_batch_size=batch_size,
            terminal_review_model=model,
            anthropic_api_key="test-key",
        )
        bot = TerminalReviewBot(
            interval_seconds=interval_seconds,
            batch_size=batch_size,
            model=model,
        )
    return bot


def _page_with_terminal_log(log_text: str = "some output") -> dict:
    """Return a mock wiki page dict containing a Terminal Log section."""
    content = (
        "---\nstatus: merged\n---\n\n"
        "# Task\n\n"
        "## Terminal Log\n\n"
        "<details>\n"
        "<summary>Full terminal output (click to expand)</summary>\n\n"
        f"```\n{log_text}\n```\n\n"
        "</details>"
    )
    return {"content": content}


def _page_with_terminal_log_and_observations(log_text: str = "some output") -> dict:
    """Return a mock wiki page that already has Grinder Observations."""
    base = _page_with_terminal_log(log_text)
    base["content"] += "\n\n## Grinder Observations\n\n- Already analyzed\n"
    return base


def _page_without_terminal_log() -> dict:
    """Return a mock wiki page with no Terminal Log section."""
    return {"content": "---\nstatus: merged\n---\n\n# Task\n\nNo terminal log here."}


def _make_anthropic_response(text: str = "- No issues found") -> MagicMock:
    """Return a mock Anthropic messages response."""
    block = MagicMock()
    block.text = text
    response = MagicMock()
    response.content = [block]
    return response


def _list_tasks_side_effect(pages: list[dict]):
    """Return a side_effect callable that yields *pages* for 'merged' and [] for 'done'.

    The bot iterates status=("done", "merged") in that order.
    """

    async def _side_effect(status: str) -> list[dict]:
        if status == "merged":
            return pages
        return []

    return _side_effect


# ---------------------------------------------------------------------------
# _extract_terminal_log
# ---------------------------------------------------------------------------


def test_extract_terminal_log_returns_log_text() -> None:
    """_extract_terminal_log extracts text from inside the fenced code block."""
    page = _page_with_terminal_log("hello world\nmore output")
    result = _extract_terminal_log(page["content"])
    assert result == "hello world\nmore output"


def test_extract_terminal_log_missing_section_returns_none() -> None:
    """_extract_terminal_log returns None when no Terminal Log section exists."""
    result = _extract_terminal_log("# Some page\n\nNo terminal log.")
    assert result is None


def test_extract_terminal_log_no_fence_returns_none() -> None:
    """_extract_terminal_log returns None when the code fence is missing."""
    content = "## Terminal Log\n\nSome text without a fence."
    assert _extract_terminal_log(content) is None


# ---------------------------------------------------------------------------
# TerminalReviewBot.run() — skip already-analyzed pages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_skips_already_analyzed_pages() -> None:
    """Bot skips pages that already have a Grinder Observations section."""
    bot = _make_bot()

    analyzed_page = _page_with_terminal_log_and_observations()

    mock_wiki = AsyncMock()
    mock_wiki.list_tasks.side_effect = _list_tasks_side_effect([{"name": "Task_0001"}])
    mock_wiki.get_page.return_value = analyzed_page
    mock_wiki.__aenter__ = AsyncMock(return_value=mock_wiki)
    mock_wiki.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("factory.bots.terminal_review.MeshWikiClient", return_value=mock_wiki),
        patch("factory.bots.terminal_review.get_settings") as mock_settings,
        patch("factory.bots.terminal_review.anthropic.AsyncAnthropic"),
    ):
        mock_settings.return_value = MagicMock(
            anthropic_api_key="test-key",
        )
        result = await bot.run()

    assert result.actions_taken == 0


@pytest.mark.asyncio
async def test_run_skips_pages_without_terminal_log() -> None:
    """Bot skips pages that have no Terminal Log section at all."""
    bot = _make_bot()

    mock_wiki = AsyncMock()
    mock_wiki.list_tasks.side_effect = _list_tasks_side_effect([{"name": "Task_0002"}])
    mock_wiki.get_page.return_value = _page_without_terminal_log()
    mock_wiki.__aenter__ = AsyncMock(return_value=mock_wiki)
    mock_wiki.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("factory.bots.terminal_review.MeshWikiClient", return_value=mock_wiki),
        patch("factory.bots.terminal_review.get_settings") as mock_settings,
        patch("factory.bots.terminal_review.anthropic.AsyncAnthropic"),
    ):
        mock_settings.return_value = MagicMock(anthropic_api_key="test-key")
        result = await bot.run()

    assert result.actions_taken == 0


# ---------------------------------------------------------------------------
# TerminalReviewBot.run() — analyze unanalyzed pages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_analyzes_unanalyzed_page() -> None:
    """Bot analyzes pages with Terminal Log but no Grinder Observations."""
    bot = _make_bot()

    mock_wiki = AsyncMock()
    mock_wiki.list_tasks.side_effect = _list_tasks_side_effect([{"name": "Task_0003"}])
    mock_wiki.get_page.return_value = _page_with_terminal_log("pip install ran ok")
    mock_wiki.append_to_page = AsyncMock()
    mock_wiki.__aenter__ = AsyncMock(return_value=mock_wiki)
    mock_wiki.__aexit__ = AsyncMock(return_value=None)

    mock_anthropic_instance = AsyncMock()
    mock_anthropic_instance.messages.create = AsyncMock(
        return_value=_make_anthropic_response("- No issues found")
    )
    mock_anthropic_cls = MagicMock(return_value=mock_anthropic_instance)

    with (
        patch("factory.bots.terminal_review.MeshWikiClient", return_value=mock_wiki),
        patch("factory.bots.terminal_review.get_settings") as mock_settings,
        patch(
            "factory.bots.terminal_review.anthropic.AsyncAnthropic",
            mock_anthropic_cls,
        ),
    ):
        mock_settings.return_value = MagicMock(anthropic_api_key="test-key")
        result = await bot.run()

    assert result.actions_taken == 1
    assert result.errors == []

    # Anthropic was called once
    mock_anthropic_instance.messages.create.assert_awaited_once()

    # append_to_page was called with the observations section
    mock_wiki.append_to_page.assert_awaited_once()
    call_args = mock_wiki.append_to_page.call_args
    assert call_args[0][0] == "Task_0003"
    assert "## Grinder Observations" in call_args[0][1]
    assert "No issues found" in call_args[0][1]


@pytest.mark.asyncio
async def test_run_calls_anthropic_with_log_text() -> None:
    """Bot includes the terminal log text in the Anthropic prompt."""
    bot = _make_bot()

    log_text = "UNIQUE_LOG_CONTENT_XYZ"
    mock_wiki = AsyncMock()
    mock_wiki.list_tasks.side_effect = _list_tasks_side_effect([{"name": "Task_0004"}])
    mock_wiki.get_page.return_value = _page_with_terminal_log(log_text)
    mock_wiki.append_to_page = AsyncMock()
    mock_wiki.__aenter__ = AsyncMock(return_value=mock_wiki)
    mock_wiki.__aexit__ = AsyncMock(return_value=None)

    mock_anthropic_instance = AsyncMock()
    mock_anthropic_instance.messages.create = AsyncMock(
        return_value=_make_anthropic_response("- Some finding")
    )
    mock_anthropic_cls = MagicMock(return_value=mock_anthropic_instance)

    with (
        patch("factory.bots.terminal_review.MeshWikiClient", return_value=mock_wiki),
        patch("factory.bots.terminal_review.get_settings") as mock_settings,
        patch(
            "factory.bots.terminal_review.anthropic.AsyncAnthropic",
            mock_anthropic_cls,
        ),
    ):
        mock_settings.return_value = MagicMock(anthropic_api_key="test-key")
        await bot.run()

    call_kwargs = mock_anthropic_instance.messages.create.call_args
    messages = call_kwargs[1]["messages"] if call_kwargs[1] else call_kwargs[0][2]
    prompt_text = messages[0]["content"]
    assert log_text in prompt_text


# ---------------------------------------------------------------------------
# TerminalReviewBot — batch cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_respects_batch_size() -> None:
    """Bot analyzes at most batch_size pages per run."""
    bot = _make_bot(batch_size=2)

    # 5 pages all unanalyzed — returned only for "merged" to avoid double-counting
    page_names = [f"Task_{i:04d}" for i in range(5)]
    mock_wiki = AsyncMock()
    mock_wiki.list_tasks.side_effect = _list_tasks_side_effect(
        [{"name": n} for n in page_names]
    )
    mock_wiki.get_page.return_value = _page_with_terminal_log("output")
    mock_wiki.append_to_page = AsyncMock()
    mock_wiki.__aenter__ = AsyncMock(return_value=mock_wiki)
    mock_wiki.__aexit__ = AsyncMock(return_value=None)

    mock_anthropic_instance = AsyncMock()
    mock_anthropic_instance.messages.create = AsyncMock(
        return_value=_make_anthropic_response("- ok")
    )
    mock_anthropic_cls = MagicMock(return_value=mock_anthropic_instance)

    with (
        patch("factory.bots.terminal_review.MeshWikiClient", return_value=mock_wiki),
        patch("factory.bots.terminal_review.get_settings") as mock_settings,
        patch(
            "factory.bots.terminal_review.anthropic.AsyncAnthropic",
            mock_anthropic_cls,
        ),
    ):
        mock_settings.return_value = MagicMock(anthropic_api_key="test-key")
        result = await bot.run()

    assert result.actions_taken == 2
    assert mock_wiki.append_to_page.await_count == 2


# ---------------------------------------------------------------------------
# TerminalReviewBot — error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_records_error_when_anthropic_fails() -> None:
    """Bot records an error per page when the Anthropic call fails, but continues."""
    bot = _make_bot(batch_size=5)

    mock_wiki = AsyncMock()
    mock_wiki.list_tasks.side_effect = _list_tasks_side_effect([{"name": "Task_0005"}])
    mock_wiki.get_page.return_value = _page_with_terminal_log("output")
    mock_wiki.append_to_page = AsyncMock()
    mock_wiki.__aenter__ = AsyncMock(return_value=mock_wiki)
    mock_wiki.__aexit__ = AsyncMock(return_value=None)

    mock_anthropic_instance = AsyncMock()
    mock_anthropic_instance.messages.create = AsyncMock(
        side_effect=RuntimeError("API error")
    )
    mock_anthropic_cls = MagicMock(return_value=mock_anthropic_instance)

    with (
        patch("factory.bots.terminal_review.MeshWikiClient", return_value=mock_wiki),
        patch("factory.bots.terminal_review.get_settings") as mock_settings,
        patch(
            "factory.bots.terminal_review.anthropic.AsyncAnthropic",
            mock_anthropic_cls,
        ),
    ):
        mock_settings.return_value = MagicMock(anthropic_api_key="test-key")
        result = await bot.run()

    assert result.actions_taken == 0
    assert len(result.errors) == 1
    assert "Task_0005" in result.errors[0]


@pytest.mark.asyncio
async def test_run_handles_list_tasks_failure_gracefully() -> None:
    """Bot records an error and returns 0 actions when list_tasks fails."""
    bot = _make_bot()

    mock_wiki = AsyncMock()
    mock_wiki.list_tasks.side_effect = RuntimeError("network error")
    mock_wiki.__aenter__ = AsyncMock(return_value=mock_wiki)
    mock_wiki.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("factory.bots.terminal_review.MeshWikiClient", return_value=mock_wiki),
        patch("factory.bots.terminal_review.get_settings") as mock_settings,
        patch("factory.bots.terminal_review.anthropic.AsyncAnthropic"),
    ):
        mock_settings.return_value = MagicMock(anthropic_api_key="test-key")
        result = await bot.run()

    assert result.actions_taken == 0
    assert len(result.errors) >= 1
