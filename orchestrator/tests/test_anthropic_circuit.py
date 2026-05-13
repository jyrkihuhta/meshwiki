"""Tests for the Anthropic circuit breaker — graceful pause when API limits hit."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import httpx
import pytest

from factory.agents import pm_agent
from factory.agents.pm_agent import (
    AnthropicBlockedError,
    _block_anthropic,
    _extract_regain_seconds,
    _is_anthropic_blocked,
    _is_billing_error,
    anthropic_block_reason,
    anthropic_blocked_seconds_remaining,
    safe_messages_create,
)
from factory.bots.base import BaseBot, BotResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reset_breaker() -> None:
    pm_agent._anthropic_blocked_until = 0.0
    pm_agent._anthropic_block_reason = ""


def _make_status_error(status_code: int, message: str) -> anthropic.APIStatusError:
    """Build an APIStatusError with controllable status + message text."""
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status_code, request=request, content=message.encode())
    return anthropic.APIStatusError(message, response=response, body=None)


@pytest.fixture(autouse=True)
def _isolate_breaker():
    """Reset the global breaker state between tests."""
    _reset_breaker()
    yield
    _reset_breaker()


# ---------------------------------------------------------------------------
# _is_billing_error
# ---------------------------------------------------------------------------


def test_is_billing_error_catches_400_invalid_request_with_usage_limit():
    """Catches the exact pattern Anthropic returned when monthly limit hit."""
    msg = (
        "Error code: 400 - {'type': 'error', 'error': {'type': "
        "'invalid_request_error', 'message': 'You have reached your specified "
        "API usage limits. You will regain access on 2026-06-01 at 00:00 UTC.'}}"
    )
    exc = _make_status_error(400, msg)
    assert _is_billing_error(exc) is True


def test_is_billing_error_catches_402_credit():
    exc = _make_status_error(402, "Your credit balance is too low")
    assert _is_billing_error(exc) is True


def test_is_billing_error_catches_429_quota():
    exc = _make_status_error(429, "Token quota exceeded")
    assert _is_billing_error(exc) is True


def test_is_billing_error_ignores_plain_400():
    """A regular 400 (e.g. malformed prompt) should NOT trip the breaker."""
    exc = _make_status_error(400, "max_tokens must be a positive integer")
    assert _is_billing_error(exc) is False


def test_is_billing_error_ignores_400_without_invalid_request_type():
    """A 400 that doesn't include 'invalid_request_error' should NOT trip."""
    exc = _make_status_error(400, "credit balance too low")  # missing type prefix
    assert _is_billing_error(exc) is False


def test_is_billing_error_ignores_500():
    """Server errors are transient — never trip the breaker."""
    exc = _make_status_error(500, "internal server error")
    assert _is_billing_error(exc) is False


def test_is_billing_error_ignores_529_overload():
    """529 overloaded is handled separately via retry — not a billing error."""
    exc = _make_status_error(529, "overloaded")
    assert _is_billing_error(exc) is False


# ---------------------------------------------------------------------------
# _extract_regain_seconds
# ---------------------------------------------------------------------------


def test_extract_regain_seconds_parses_future_date():
    """Parses 'regain access on YYYY-MM-DD at HH:MM UTC' to seconds until then."""
    future = datetime.now(timezone.utc) + timedelta(days=10)
    msg = (
        f"You have reached your specified API usage limits. You will regain "
        f"access on {future.strftime('%Y-%m-%d')} at {future.strftime('%H:%M')} UTC."
    )
    exc = _make_status_error(400, msg)
    seconds = _extract_regain_seconds(exc)
    # Should be ~10 days = 864000s. Allow a tolerant window for rounding /
    # clock skew between datetime.now() and the parsed minute boundary.
    assert 9 * 86400 < seconds < 11 * 86400


def test_extract_regain_seconds_clamps_past_date_to_zero():
    """A regain date in the past returns 0 (don't block forever in the past)."""
    past = datetime.now(timezone.utc) - timedelta(days=1)
    msg = (
        f"You will regain access on {past.strftime('%Y-%m-%d')} at "
        f"{past.strftime('%H:%M')} UTC."
    )
    exc = _make_status_error(400, msg)
    assert _extract_regain_seconds(exc) == 0.0


def test_extract_regain_seconds_returns_zero_when_no_match():
    exc = _make_status_error(429, "Rate limit exceeded")
    assert _extract_regain_seconds(exc) == 0.0


def test_extract_regain_seconds_handles_malformed_date():
    exc = _make_status_error(400, "regain access on 2026-13-99 at 99:99 UTC")
    assert _extract_regain_seconds(exc) == 0.0


# ---------------------------------------------------------------------------
# _block_anthropic / _is_anthropic_blocked
# ---------------------------------------------------------------------------


def test_block_anthropic_engages_breaker():
    assert _is_anthropic_blocked() is False
    _block_anthropic(seconds=120.0, reason="test")
    assert _is_anthropic_blocked() is True
    assert anthropic_block_reason() == "test"
    remaining = anthropic_blocked_seconds_remaining()
    assert 119 < remaining <= 120


def test_block_anthropic_clamps_minimum():
    """Even with seconds=0, the breaker engages for at least 60s (avoid races).

    Allow a small tolerance for clock drift between _block_anthropic() returning
    and us reading the remaining seconds.
    """
    _block_anthropic(seconds=0.0)
    assert anthropic_blocked_seconds_remaining() >= 59.0


def test_block_anthropic_clamps_maximum():
    """Cap at 30 days to avoid permanent blocks from misparsed dates."""
    _block_anthropic(seconds=365 * 86400.0)  # 1 year
    assert anthropic_blocked_seconds_remaining() <= 30 * 86400.0


def test_block_anthropic_truncates_long_reason():
    long_reason = "x" * 500
    _block_anthropic(seconds=60.0, reason=long_reason)
    assert len(anthropic_block_reason()) <= 300


# ---------------------------------------------------------------------------
# safe_messages_create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_safe_messages_create_raises_when_breaker_tripped():
    _block_anthropic(seconds=120.0, reason="pre-tripped")
    client = MagicMock()
    client.messages.create = AsyncMock()
    with pytest.raises(AnthropicBlockedError) as exc_info:
        await safe_messages_create(client, model="m", max_tokens=10, messages=[])
    assert "pre-tripped" in str(exc_info.value)
    client.messages.create.assert_not_called()  # didn't even attempt the API


@pytest.mark.asyncio
async def test_safe_messages_create_passes_through_on_success():
    client = MagicMock()
    client.messages.create = AsyncMock(return_value="ok-response")
    result = await safe_messages_create(client, model="m", max_tokens=10, messages=[])
    assert result == "ok-response"
    assert not _is_anthropic_blocked()


@pytest.mark.asyncio
async def test_safe_messages_create_trips_breaker_on_billing_error():
    """The exact pattern we saw on the VPS should trip the breaker."""
    msg = (
        "Error code: 400 - {'type': 'error', 'error': {'type': "
        "'invalid_request_error', 'message': 'You have reached your specified "
        "API usage limits. You will regain access on 2026-06-01 at 00:00 UTC.'}}"
    )
    client = MagicMock()
    client.messages.create = AsyncMock(side_effect=_make_status_error(400, msg))

    with pytest.raises(anthropic.APIStatusError):
        await safe_messages_create(client, model="m", max_tokens=10, messages=[])

    assert _is_anthropic_blocked() is True
    # And remaining time should be the parsed regain date, not the default 900s
    assert anthropic_blocked_seconds_remaining() > 900.0


@pytest.mark.asyncio
async def test_safe_messages_create_does_not_trip_on_transient_error():
    """Non-billing errors should propagate without tripping the breaker."""
    client = MagicMock()
    client.messages.create = AsyncMock(
        side_effect=_make_status_error(529, "overloaded")
    )
    with pytest.raises(anthropic.APIStatusError):
        await safe_messages_create(client, model="m", max_tokens=10, messages=[])
    assert _is_anthropic_blocked() is False


# ---------------------------------------------------------------------------
# BaseBot._loop pause-on-block behaviour
# ---------------------------------------------------------------------------


class _AnthropicBot(BaseBot):
    """Bot that marks itself as pausing on Anthropic block."""

    name = "test-anthropic-bot"
    interval_seconds = 1
    pauses_on_anthropic_block = True

    def __init__(self) -> None:
        super().__init__()
        self.run_count = 0

    async def run(self) -> BotResult:
        self.run_count += 1
        return BotResult(ran_at=0.0, actions_taken=1)


class _NonAnthropicBot(BaseBot):
    """Bot that does NOT pause on Anthropic block (e.g. scheduler / bookkeeper)."""

    name = "test-non-anthropic-bot"
    interval_seconds = 1
    pauses_on_anthropic_block = False

    def __init__(self) -> None:
        super().__init__()
        self.run_count = 0

    async def run(self) -> BotResult:
        self.run_count += 1
        return BotResult(ran_at=0.0, actions_taken=1)


@pytest.mark.asyncio
async def test_loop_skips_run_when_breaker_tripped_and_bot_pauses():
    """An Anthropic-dependent bot should skip run() while the breaker is tripped."""
    _block_anthropic(seconds=120.0, reason="test")

    bot = _AnthropicBot()
    with patch.object(BaseBot, "_update_bot_page", new=AsyncMock()):
        await bot.start()
        # Give the loop two ticks to confirm it skipped both.
        await asyncio.sleep(1.5)
        await bot.stop()

    assert bot.run_count == 0
    # last_result should describe the pause
    assert bot.last_result is not None
    assert "paused" in bot.last_result.details
    assert "Anthropic blocked" in bot.last_result.details


@pytest.mark.asyncio
async def test_loop_runs_normally_for_non_anthropic_bot_even_when_blocked():
    """Bots like scheduler / bookkeeper keep working even when Anthropic is blocked."""
    _block_anthropic(seconds=120.0, reason="test")

    bot = _NonAnthropicBot()
    with patch.object(BaseBot, "_update_bot_page", new=AsyncMock()):
        await bot.start()
        await asyncio.sleep(1.2)
        await bot.stop()

    assert bot.run_count >= 1  # ran at least once


@pytest.mark.asyncio
async def test_loop_resumes_when_breaker_clears():
    """After the breaker expires, an Anthropic-dependent bot starts running again."""
    bot = _AnthropicBot()
    # Block for the minimum window (60s clamp), but immediately fake-expire it.
    _block_anthropic(seconds=120.0, reason="test")
    with patch.object(BaseBot, "_update_bot_page", new=AsyncMock()):
        await bot.start()
        await asyncio.sleep(0.3)
        assert bot.run_count == 0  # paused

        _reset_breaker()  # simulate the cooldown expiring
        await asyncio.sleep(1.3)  # one more tick interval
        await bot.stop()

    assert bot.run_count >= 1  # resumed
