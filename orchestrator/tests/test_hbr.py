"""Tests for the HBR resource manager (hbr.py).

Tests use HBRManager directly — not the module-level singleton — to
avoid cross-test state leakage.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pytest

from factory.hbr import HBRManager


class TestCanAllocateSandbox:
    def test_no_budget_always_allows(self) -> None:
        hbr = HBRManager(daily_budget_usd=0.0)
        assert hbr.can_allocate_sandbox() is True

    def test_negative_budget_always_allows(self) -> None:
        hbr = HBRManager(daily_budget_usd=-1.0)
        assert hbr.can_allocate_sandbox() is True

    def test_under_budget_allows(self) -> None:
        hbr = HBRManager(daily_budget_usd=10.0)
        hbr.record_cost("model-a", 5.0)
        assert hbr.can_allocate_sandbox() is True

    def test_at_budget_blocks(self) -> None:
        hbr = HBRManager(daily_budget_usd=5.0)
        hbr.record_cost("model-a", 5.0)
        assert hbr.can_allocate_sandbox() is False

    def test_over_budget_blocks(self) -> None:
        hbr = HBRManager(daily_budget_usd=5.0)
        hbr.record_cost("model-a", 6.0)
        assert hbr.can_allocate_sandbox() is False

    def test_multiple_records_accumulate(self) -> None:
        hbr = HBRManager(daily_budget_usd=10.0)
        hbr.record_cost("model-a", 4.0)
        hbr.record_cost("model-b", 3.0)
        assert hbr.can_allocate_sandbox() is True
        hbr.record_cost("model-c", 4.0)
        assert hbr.can_allocate_sandbox() is False


class TestRecordCost:
    def test_zero_cost_ignored(self) -> None:
        hbr = HBRManager(daily_budget_usd=1.0)
        hbr.record_cost("model-a", 0.0)
        assert hbr.status()["daily_cost_usd"] == 0.0
        assert "model-a" not in hbr.status()["model_usage"]

    def test_negative_cost_ignored(self) -> None:
        hbr = HBRManager(daily_budget_usd=1.0)
        hbr.record_cost("model-a", -1.0)
        assert hbr.status()["daily_cost_usd"] == 0.0

    def test_cost_accumulates_across_models(self) -> None:
        hbr = HBRManager()
        hbr.record_cost("sonnet", 0.25, input_tokens=50_000, output_tokens=5_000)
        hbr.record_cost("haiku", 0.01, input_tokens=10_000, output_tokens=512)
        status = hbr.status()
        assert abs(status["daily_cost_usd"] - 0.26) < 1e-6
        assert "sonnet" in status["model_usage"]
        assert "haiku" in status["model_usage"]

    def test_same_model_tokens_accumulate(self) -> None:
        hbr = HBRManager()
        hbr.record_cost("sonnet", 0.10, input_tokens=10_000, output_tokens=1_000)
        hbr.record_cost("sonnet", 0.20, input_tokens=20_000, output_tokens=2_000)
        usage = hbr.status()["model_usage"]["sonnet"]
        assert usage["input_tokens"] == 30_000
        assert usage["output_tokens"] == 3_000
        assert abs(usage["cost_usd"] - 0.30) < 1e-6

    def test_sandbox_cost_recorded_without_tokens(self) -> None:
        hbr = HBRManager()
        hbr.record_cost("e2b", 0.05)
        usage = hbr.status()["model_usage"]["e2b"]
        assert usage["input_tokens"] == 0
        assert usage["output_tokens"] == 0
        assert abs(usage["cost_usd"] - 0.05) < 1e-6


class TestBudgetRemainingUsd:
    def test_no_budget_returns_none(self) -> None:
        hbr = HBRManager(daily_budget_usd=0.0)
        assert hbr.budget_remaining_usd() is None

    def test_remaining_decreases_with_spend(self) -> None:
        hbr = HBRManager(daily_budget_usd=10.0)
        hbr.record_cost("m", 3.0)
        assert abs(hbr.budget_remaining_usd() - 7.0) < 1e-9

    def test_remaining_floors_at_zero(self) -> None:
        hbr = HBRManager(daily_budget_usd=5.0)
        hbr.record_cost("m", 10.0)
        assert hbr.budget_remaining_usd() == 0.0


class TestDailyReset:
    def test_cost_resets_on_new_day(self) -> None:
        hbr = HBRManager(daily_budget_usd=5.0)
        hbr.record_cost("m", 4.9)
        assert hbr.can_allocate_sandbox() is True

        yesterday = date.today() - timedelta(days=1)
        hbr._budget_day = yesterday  # force "yesterday" state

        # After reset, cost should be zero and allocation allowed again.
        assert hbr.can_allocate_sandbox() is True
        assert hbr.status()["daily_cost_usd"] == 0.0

    def test_model_usage_cleared_on_reset(self) -> None:
        hbr = HBRManager()
        hbr.record_cost("sonnet", 0.10, input_tokens=1000, output_tokens=500)
        hbr._budget_day = date.today() - timedelta(days=1)
        hbr._reset_if_new_day()
        assert hbr.status()["model_usage"] == {}


class TestStatus:
    def test_status_shape(self) -> None:
        hbr = HBRManager(daily_budget_usd=20.0)
        hbr.record_cost("sonnet", 1.5, input_tokens=100_000, output_tokens=10_000)
        s = hbr.status()
        assert s["budget_enabled"] is True
        assert abs(s["daily_cost_usd"] - 1.5) < 1e-6
        assert s["daily_budget_usd"] == 20.0
        assert abs(s["budget_remaining_usd"] - 18.5) < 1e-6
        assert "budget_reset_at" in s
        assert "model_usage" in s

    def test_status_no_budget(self) -> None:
        hbr = HBRManager(daily_budget_usd=0.0)
        s = hbr.status()
        assert s["budget_enabled"] is False
        assert s["budget_remaining_usd"] is None

    def test_budget_reset_at_is_tomorrow_midnight_utc(self) -> None:
        hbr = HBRManager()
        s = hbr.status()
        reset_at = s["budget_reset_at"]
        assert "T00:00:00" in reset_at
        assert "+00:00" in reset_at

    def test_cost_rounded_to_6_decimals(self) -> None:
        hbr = HBRManager()
        hbr.record_cost("m", 1 / 3)
        s = hbr.status()
        cost_str = str(s["daily_cost_usd"])
        decimal_places = len(cost_str.split(".")[-1]) if "." in cost_str else 0
        assert decimal_places <= 6
