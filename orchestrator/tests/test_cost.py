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

    def test_model_rates_contains_current_default_models(self):
        """Default config models must have pricing so cost_usd is non-zero."""
        assert "claude-sonnet-4-6" in MODEL_RATES
        assert "claude-haiku-4-5-20251001" in MODEL_RATES
        assert "MiniMax-M2.7" in MODEL_RATES

    def test_model_rates_format(self):
        for model, rates in MODEL_RATES.items():
            assert isinstance(rates, tuple)
            assert len(rates) == 2
            input_rate, output_rate = rates
            assert input_rate > 0
            assert output_rate > 0

    def test_tokens_to_usd_sonnet_4_6(self):
        usage = SimpleNamespace(input_tokens=1_000_000, output_tokens=1_000_000)
        cost = tokens_to_usd(usage, "claude-sonnet-4-6")
        assert cost > 0, "claude-sonnet-4-6 cost must be non-zero"

    def test_tokens_to_usd_haiku_4_5(self):
        usage = SimpleNamespace(input_tokens=1_000_000, output_tokens=1_000_000)
        cost = tokens_to_usd(usage, "claude-haiku-4-5-20251001")
        assert cost > 0, "claude-haiku-4-5-20251001 cost must be non-zero"

    def test_tokens_to_usd_minimax(self):
        usage = SimpleNamespace(input_tokens=1_000_000, output_tokens=1_000_000)
        cost = tokens_to_usd(usage, "MiniMax-M2.7")
        assert cost > 0, "MiniMax-M2.7 cost must be non-zero"


class TestOpenAIResponseAdapter:
    """Test that _OpenAIResponseAdapter exposes usage for cost tracking."""

    def _make_oai_resp(self, prompt_tokens: int, completion_tokens: int):
        from types import SimpleNamespace

        usage = SimpleNamespace(
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
        )
        tool_call = None
        msg = SimpleNamespace(content="hello", tool_calls=[])
        choice = SimpleNamespace(message=msg, finish_reason="stop")
        return SimpleNamespace(choices=[choice], usage=usage)

    def test_usage_exposed_with_correct_field_names(self):
        from factory.agents.pm_agent import _OpenAIResponseAdapter

        oai = self._make_oai_resp(100, 50)
        adapter = _OpenAIResponseAdapter(oai)
        assert adapter.usage is not None
        assert adapter.usage.input_tokens == 100
        assert adapter.usage.output_tokens == 50

    def test_response_model_is_minimax(self):
        from factory.agents.pm_agent import _OpenAIResponseAdapter

        oai = self._make_oai_resp(100, 50)
        adapter = _OpenAIResponseAdapter(oai)
        assert adapter._response_model == "MiniMax-M2.7"

    def test_minimax_fallback_cost_uses_minimax_rates(self):
        """Cost calculated from adapter usage must use MiniMax pricing."""
        from factory.agents.pm_agent import _OpenAIResponseAdapter

        oai = self._make_oai_resp(1_000_000, 1_000_000)
        adapter = _OpenAIResponseAdapter(oai)
        effective_model = getattr(adapter, "_response_model", "claude-sonnet-4-6")
        cost = tokens_to_usd(adapter.usage, effective_model)
        minimax_cost = tokens_to_usd(
            SimpleNamespace(input_tokens=1_000_000, output_tokens=1_000_000),
            "MiniMax-M2.7",
        )
        assert abs(cost - minimax_cost) < 1e-9

    def test_usage_none_when_no_usage_on_response(self):
        from factory.agents.pm_agent import _OpenAIResponseAdapter

        msg = SimpleNamespace(content="hi", tool_calls=[])
        choice = SimpleNamespace(message=msg, finish_reason="stop")
        oai = SimpleNamespace(choices=[choice], usage=None)
        adapter = _OpenAIResponseAdapter(oai)
        assert adapter.usage is None
