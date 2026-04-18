"""Helpers for computing USD cost from Anthropic token usage and E2B sandbox time.

Pure functions only — no I/O, no LangGraph imports.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# (input_usd_per_token, output_usd_per_token)
# Anthropic pricing: https://www.anthropic.com/pricing
MODEL_RATES: dict[str, tuple[float, float]] = {
    "claude-3-5-sonnet-20241022": (3e-6, 15e-6),
    "claude-3-haiku-20240307": (0.25e-6, 1.25e-6),
}

E2B_COST_PER_SECOND: float = 0.000014


def tokens_to_usd(usage: Any, model: str) -> float:
    """Compute USD cost from an Anthropic response usage object.

    Args:
        usage: anthropic response.usage object with .input_tokens / .output_tokens
        model: Anthropic model identifier string

    Returns:
        Cost in USD (float). Returns 0.0 for unknown models with a warning.
    """
    rates = MODEL_RATES.get(model)
    if rates is None:
        logger.warning("Unknown model %r — cost tracking skipped", model)
        return 0.0
    input_rate, output_rate = rates
    input_tokens = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "output_tokens", None)
    if not isinstance(input_tokens, (int, float)) or not isinstance(
        output_tokens, (int, float)
    ):
        return 0.0
    return input_tokens * input_rate + output_tokens * output_rate


def sandbox_time_to_usd(elapsed_seconds: float) -> float:
    """Convert E2B sandbox wall-clock time to USD.

    Args:
        elapsed_seconds: how long the sandbox ran

    Returns:
        Cost in USD
    """
    return elapsed_seconds * E2B_COST_PER_SECOND
