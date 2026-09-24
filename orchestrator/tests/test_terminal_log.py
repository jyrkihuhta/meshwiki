"""Tests for terminal log persistence in the grinder agent."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from factory.agents.grinder_agent import (
    _persist_terminal_log,
    _strip_ansi,
    _truncate_log,
)

# ---------------------------------------------------------------------------
# _strip_ansi
# ---------------------------------------------------------------------------


def test_strip_ansi_removes_colour_codes() -> None:
    """_strip_ansi removes common SGR colour sequences."""
    raw = "\x1b[31mred text\x1b[0m normal"
    assert _strip_ansi(raw) == "red text normal"


def test_strip_ansi_removes_bold_and_reset() -> None:
    """_strip_ansi removes bold (1m) and reset (0m) codes."""
    raw = "\x1b[1mbold\x1b[0m"
    assert _strip_ansi(raw) == "bold"


def test_strip_ansi_removes_256_colour() -> None:
    """_strip_ansi handles multi-parameter SGR sequences."""
    raw = "\x1b[38;5;196mhello\x1b[0m"
    assert _strip_ansi(raw) == "hello"


def test_strip_ansi_plain_text_unchanged() -> None:
    """_strip_ansi leaves plain text untouched."""
    text = "no escape codes here"
    assert _strip_ansi(text) == text


def test_strip_ansi_empty_string() -> None:
    """_strip_ansi handles empty input."""
    assert _strip_ansi("") == ""


def test_strip_ansi_mixed_content() -> None:
    """_strip_ansi handles a realistic terminal line."""
    raw = "\x1b[32m✓\x1b[0m Tests passed (42/42)\r\n"
    result = _strip_ansi(raw)
    assert "\x1b" not in result
    assert "Tests passed (42/42)" in result


# ---------------------------------------------------------------------------
# _truncate_log
# ---------------------------------------------------------------------------


def test_truncate_log_short_text_unchanged() -> None:
    """_truncate_log returns text as-is when shorter than max_chars."""
    text = "short text"
    assert _truncate_log(text, 100) == text


def test_truncate_log_exact_length_unchanged() -> None:
    """_truncate_log returns text as-is when equal to max_chars."""
    text = "exact"
    assert _truncate_log(text, 5) == text


def test_truncate_log_keeps_tail() -> None:
    """_truncate_log keeps the LAST max_chars characters."""
    text = "AAAA" + "BBBB"
    result = _truncate_log(text, 4)
    assert result == "BBBB"


def test_truncate_log_zero_max_returns_empty() -> None:
    """_truncate_log with max_chars=0 returns empty string."""
    assert _truncate_log("hello", 0) == ""


# ---------------------------------------------------------------------------
# _persist_terminal_log
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_terminal_log_calls_append_to_page() -> None:
    """_persist_terminal_log appends a Terminal Log block to the wiki page."""
    mock_client = AsyncMock()

    await _persist_terminal_log(
        mock_client,
        "Task_0042_Sub_01",
        "some output\r\n",
        max_chars=10000,
    )

    mock_client.append_to_page.assert_awaited_once()
    call_args = mock_client.append_to_page.call_args
    page_name = call_args[0][0]
    content = call_args[0][1]

    assert page_name == "Task_0042_Sub_01"
    assert "## Terminal Log" in content
    assert "<details>" in content
    assert "some output" in content


@pytest.mark.asyncio
async def test_persist_terminal_log_strips_ansi() -> None:
    """_persist_terminal_log strips ANSI codes from the persisted content."""
    mock_client = AsyncMock()

    await _persist_terminal_log(
        mock_client,
        "Task_0042_Sub_01",
        "\x1b[32mgreen text\x1b[0m",
        max_chars=10000,
    )

    content = mock_client.append_to_page.call_args[0][1]
    assert "\x1b" not in content
    assert "green text" in content


@pytest.mark.asyncio
async def test_persist_terminal_log_truncates_long_output() -> None:
    """_persist_terminal_log truncates output exceeding max_chars."""
    mock_client = AsyncMock()
    long_output = "A" * 5000 + "B" * 5000

    await _persist_terminal_log(
        mock_client,
        "Task_0042_Sub_01",
        long_output,
        max_chars=100,
    )

    content = mock_client.append_to_page.call_args[0][1]
    # The persisted content should only contain the tail "B"s, not the leading "A"s
    assert "A" not in content
    assert "B" * 100 in content


@pytest.mark.asyncio
async def test_persist_terminal_log_is_fire_and_forget() -> None:
    """_persist_terminal_log does not raise even when append_to_page fails."""
    mock_client = AsyncMock()
    mock_client.append_to_page.side_effect = RuntimeError("wiki down")

    # Must not raise
    await _persist_terminal_log(
        mock_client,
        "Task_0042_Sub_01",
        "some output",
        max_chars=10000,
    )


@pytest.mark.asyncio
async def test_persist_terminal_log_includes_details_block() -> None:
    """_persist_terminal_log wraps output in a collapsible <details> block."""
    mock_client = AsyncMock()

    await _persist_terminal_log(
        mock_client,
        "AnyPage",
        "hello",
        max_chars=10000,
    )

    content = mock_client.append_to_page.call_args[0][1]
    assert "<details>" in content
    assert "</details>" in content
    assert "<summary>" in content
