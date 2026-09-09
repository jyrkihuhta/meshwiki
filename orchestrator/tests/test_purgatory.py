"""Tests for the purgatory bot (deck PLAN.md M2.5, tier 1)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from factory.bots.purgatory import TIER1_TARGETS, PurgatoryBot, summarize_coverage


# ---- summarize_coverage (pure helper) ---------------------------------


def _status(targets: list[dict]) -> dict:
    return {"heartbeat": {"coverage": {"targets": targets}}}


def test_summarize_coverage_none_status():
    summary, errors = summarize_coverage(None, TIER1_TARGETS)
    assert "no status" in summary
    assert errors


def test_summarize_coverage_all_healthy():
    targets = [
        {"handle": h, "status": "new", "checks": {"total": 10, "complete": 5, "confirmed": 1, "failed": 2}}
        for h in TIER1_TARGETS
    ]
    summary, errors = summarize_coverage(_status(targets), TIER1_TARGETS)
    assert errors == []
    assert f"{len(TIER1_TARGETS)}/{len(TIER1_TARGETS)} targets known" in summary
    assert f"{10 * len(TIER1_TARGETS)} checks matched" in summary


def test_summarize_coverage_flags_zero_checks_after_discovery():
    targets = [
        {"handle": h, "status": "new", "checks": {"total": 10, "complete": 5, "confirmed": 0, "failed": 0}}
        for h in TIER1_TARGETS[:-1]
    ]
    # Last target was discovered (status "new") but matched nothing — the
    # exact failure mode this bot exists to catch.
    targets.append({"handle": TIER1_TARGETS[-1], "status": "new", "checks": {"total": 0}})
    summary, errors = summarize_coverage(_status(targets), TIER1_TARGETS)
    assert any("matched 0 checks" in e for e in errors)
    assert TIER1_TARGETS[-1] in errors[0]


def test_summarize_coverage_does_not_flag_not_discovered_yet():
    """A target that simply hasn't had its DISCOVER tick this round (bounded
    window, round-robin scheduler) isn't broken — don't false-positive it."""
    targets = [
        {"handle": h, "status": "new", "checks": {"total": 10, "complete": 5}}
        for h in TIER1_TARGETS[:-1]
    ]
    targets.append({"handle": TIER1_TARGETS[-1], "status": "not_discovered"})
    summary, errors = summarize_coverage(_status(targets), TIER1_TARGETS)
    assert errors == []


def test_summarize_coverage_flags_missing_target():
    targets = [
        {"handle": h, "status": "new", "checks": {"total": 10}}
        for h in TIER1_TARGETS[:-1]
    ]
    # dvga entirely absent from Molly's own status — config drift
    summary, errors = summarize_coverage(_status(targets), TIER1_TARGETS)
    assert any("missing from Molly's config" in e and TIER1_TARGETS[-1] in e for e in errors)


# ---- PurgatoryBot.run() (I/O mocked) -----------------------------------


@pytest.mark.asyncio
async def test_run_full_cycle_arms_runs_disarms():
    bot = PurgatoryBot(interval_seconds=1, run_window_seconds=0, molly_url="http://molly:8780")
    bot._molly_post = AsyncMock(return_value={"status": "ok"})
    bot._molly_get = AsyncMock(return_value={
        "heartbeat": {"coverage": {"targets": [
            {"handle": h, "status": "new", "checks": {"total": 5, "complete": 1}}
            for h in TIER1_TARGETS
        ]}}
    })

    result = await bot.run()

    assert result.actions_taken == 1
    assert result.errors == []
    # 7 arms + heartbeat start + heartbeat stop + 7 disarms = 16 POSTs
    assert bot._molly_post.await_count == 2 * len(TIER1_TARGETS) + 2
    arm_calls = [c.args[0] for c in bot._molly_post.await_args_list if "/arm" in c.args[0]]
    disarm_calls = [c.args[0] for c in bot._molly_post.await_args_list if "/disarm" in c.args[0]]
    assert len(arm_calls) == len(TIER1_TARGETS)
    assert len(disarm_calls) == len(TIER1_TARGETS)
    assert "checks matched" in result.details


@pytest.mark.asyncio
async def test_run_aborts_if_no_targets_can_be_armed():
    bot = PurgatoryBot(interval_seconds=1, run_window_seconds=0, molly_url="http://molly:8780")
    bot._molly_post = AsyncMock(side_effect=RuntimeError("connection refused"))
    bot._molly_get = AsyncMock()

    result = await bot.run()

    assert result.actions_taken == 0
    assert "aborted" in result.details
    bot._molly_get.assert_not_awaited()  # never got to the status-fetch step


@pytest.mark.asyncio
async def test_run_survives_individual_disarm_failure():
    """A single disarm call failing must not stop the rest — and must not
    leave the run silently "clean" either; it should surface as an error."""
    bot = PurgatoryBot(interval_seconds=1, run_window_seconds=0, molly_url="http://molly:8780")

    async def flaky_post(path: str):
        if path == "/targets/jwtlab/disarm":
            raise RuntimeError("timeout")
        return {"status": "ok"}

    bot._molly_post = AsyncMock(side_effect=flaky_post)
    bot._molly_get = AsyncMock(return_value={"heartbeat": {"coverage": {"targets": []}}})

    result = await bot.run()

    assert result.actions_taken == 1
    assert any("disarm jwtlab" in e for e in result.errors)
    # Every other target still got its disarm call despite the one failure.
    disarm_calls = [c.args[0] for c in bot._molly_post.await_args_list if "/disarm" in c.args[0]]
    assert len(disarm_calls) == len(TIER1_TARGETS)


@pytest.mark.asyncio
async def test_run_reports_zero_check_targets_as_errors():
    bot = PurgatoryBot(interval_seconds=1, run_window_seconds=0, molly_url="http://molly:8780")
    bot._molly_post = AsyncMock(return_value={"status": "ok"})
    targets = [
        {"handle": h, "status": "new", "checks": {"total": 5}}
        for h in TIER1_TARGETS[:-1]
    ]
    targets.append({"handle": "dvga", "status": "new", "checks": {"total": 0}})
    bot._molly_get = AsyncMock(return_value={"heartbeat": {"coverage": {"targets": targets}}})

    result = await bot.run()

    assert any("dvga" in e for e in result.errors)


def test_default_construction_reads_settings():
    with patch("factory.bots.purgatory.get_settings") as mock_settings:
        s = mock_settings.return_value
        s.purgatory_interval_seconds = 86400
        s.purgatory_run_window_seconds = 600
        s.molly_url = "http://molly:8780"
        bot = PurgatoryBot()
    assert bot.interval_seconds == 86400
    assert bot._run_window_seconds == 600
    assert bot._molly_url == "http://molly:8780"


def test_default_molly_url_falls_back_when_unset():
    with patch("factory.bots.purgatory.get_settings") as mock_settings:
        s = mock_settings.return_value
        s.purgatory_interval_seconds = 86400
        s.purgatory_run_window_seconds = 600
        s.molly_url = ""
        bot = PurgatoryBot()
    assert bot._molly_url == "http://molly:8780"
