"""
Per-million-token input pricing in USD for cost-savings calculations.

Numbers are public list prices for the most common chat models. Override
or extend via ContextOptimizer(pricing=...) when running behind a
re-priced gateway.
"""

from __future__ import annotations

# USD per 1,000,000 input tokens (as of public OpenAI/Anthropic price lists).
# Output pricing is intentionally omitted: the optimizer only changes the
# *input* size (it doesn't compress the model's response).
INPUT_USD_PER_MILLION: dict[str, float] = {
    # OpenAI
    "gpt-4o": 2.50,
    "gpt-4o-2024-08-06": 2.50,
    "gpt-4o-mini": 0.15,
    "gpt-4o-mini-2024-07-18": 0.15,
    "gpt-4-turbo": 10.00,
    "gpt-4": 30.00,
    "gpt-3.5-turbo": 0.50,
    "o1": 15.00,
    "o1-mini": 3.00,
    "o3-mini": 1.10,
    # Anthropic (informational; model_id won't normally be claude here)
    "claude-3-5-sonnet-latest": 3.00,
    "claude-3-5-haiku-latest": 0.80,
    "claude-3-opus-latest": 15.00,
}


def input_usd_per_million(model: str) -> float | None:
    """Return USD per 1M input tokens for `model`, or None if unknown."""
    if model in INPUT_USD_PER_MILLION:
        return INPUT_USD_PER_MILLION[model]
    # Cheap prefix match: gpt-4o-2025-xx-yy -> gpt-4o
    for known in INPUT_USD_PER_MILLION:
        if model.startswith(known):
            return INPUT_USD_PER_MILLION[known]
    return None


def usd_for_tokens(tokens: int, model: str) -> float | None:
    rate = input_usd_per_million(model)
    if rate is None:
        return None
    return tokens / 1_000_000 * rate
