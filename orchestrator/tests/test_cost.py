"""Tests for cost tracking helpers (cost.py)."""

from __future__ import annotations

from types import SimpleNamespace

from factory.cost import (
    E2B_COST_PER_SECOND,
    MODEL_RATES,
    sandbox_time_to_usd,
    tokens_to_usd,
)


class TestTokensToUsd:
    def test_tokens_to_usd_sonnet(self):
        usage = SimpleNamespace(input_tokens=1_000_000, output_tokens=1_000_000)
        cost = tokens_to_usd(usage, "claude-3-5-sonnet-20241022")
        assert abs(cost - 18.0) < 1e-6

    def test_tokens_to_usd_haiku(self):
        usage = SimpleNamespace(input_tokens=1_000_000, output_tokens=1_000_000)
        cost = tokens_to_usd(usage, "claude-3-haiku-20240307")
        assert abs(cost - 1.5) < 1e-6

    def test_tokens_to_usd_unknown_model_returns_zero(self):
        usage = SimpleNamespace(input_tokens=1000, output_tokens=500)
        assert tokens_to_usd(usage, "unknown-model-xyz") == 0.0

    def test_tokens_to_usd_zero_tokens(self):
        usage = SimpleNamespace(input_tokens=0, output_tokens=0)
        cost = tokens_to_usd(usage, "claude-3-5-sonnet-20241022")
        assert cost == 0.0

    def test_tokens_to_usd_different_ratios(self):
        usage = SimpleNamespace(input_tokens=500_000, output_tokens=100_000)
        cost = tokens_to_usd(usage, "claude-3-5-sonnet-20241022")
        expected = 500_000 * 3e-6 + 100_000 * 15e-6
        assert abs(cost - expected) < 1e-6


class TestSandboxTimeToUsd:
    def test_sandbox_time_to_usd(self):
        cost = sandbox_time_to_usd(3600)
        assert cost > 0
        assert abs(cost - 3600 * E2B_COST_PER_SECOND) < 1e-9

    def test_sandbox_time_to_usd_zero(self):
        assert sandbox_time_to_usd(0) == 0.0

    def test_sandbox_time_to_usd_one_second(self):
        cost = sandbox_time_to_usd(1)
        assert abs(cost - E2B_COST_PER_SECOND) < 1e-12


class TestModelRates:
    def test_model_rates_contains_required_models(self):
        assert "claude-3-5-sonnet-20241022" in MODEL_RATES
        assert "claude-3-haiku-20240307" in MODEL_RATES

    def test_model_rates_format(self):
        for model, rates in MODEL_RATES.items():
            assert isinstance(rates, tuple)
            assert len(rates) == 2
            input_rate, output_rate = rates
            assert input_rate > 0
            assert output_rate > 0
