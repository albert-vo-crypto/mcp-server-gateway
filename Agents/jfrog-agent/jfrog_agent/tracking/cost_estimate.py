"""
Rough LLM USD cost from token counts + model name.

Langfuse/LangSmith show their own estimates from traces; this is an independent
ballpark for BigQuery so `latency_cost.estimated_cost_usd` is populated when
we have token totals. Override with env for your org's contracted rates:

  AGENT_LLM_INPUT_USD_PER_1M   (e.g. 0.15)
  AGENT_LLM_OUTPUT_USD_PER_1M  (e.g. 0.60)
"""

from __future__ import annotations

import os
from typing import Optional


def _per_million_rates_for_model(model_id: Optional[str]) -> tuple[float, float] | None:
    """Return (input_usd_per_1m, output_usd_per_1m) or None to skip estimate."""
    inp_env = os.getenv("AGENT_LLM_INPUT_USD_PER_1M", "").strip()
    out_env = os.getenv("AGENT_LLM_OUTPUT_USD_PER_1M", "").strip()
    if inp_env and out_env:
        try:
            return float(inp_env), float(out_env)
        except ValueError:
            pass

    if not model_id:
        # BQ row sometimes omits model_name; use mini-tier rates when we still have tokens.
        return 0.15, 0.60
    m = model_id.lower()
    # Approximate public list prices (USD / 1M tokens); update periodically or use env.
    if "gpt-4o-mini" in m or "4o-mini" in m:
        return 0.15, 0.60
    if "gpt-4o" in m and "mini" not in m:
        return 2.50, 10.00
    if "gpt-4-turbo" in m or "gpt-4-0125" in m or "gpt-4-1106" in m:
        return 10.00, 30.00
    if "gpt-3.5" in m:
        return 0.50, 1.50
    if "gpt-4" in m:
        return 30.00, 60.00
    if m.startswith("gpt-"):
        return 0.15, 0.60
    return None


def estimate_llm_cost_usd(
    model_id: Optional[str],
    input_tokens: Optional[int],
    output_tokens: Optional[int],
    total_tokens: Optional[int] = None,
) -> Optional[float]:
    """
    Estimated spend for chat-completions-style billing from prompt + completion tokens.
    Returns None when token counts are missing or zero, or when pricing is unknown.
    If only ``total_tokens`` is set (no per-direction counts), uses a 3/4 input vs 1/4
    output split for pricing (rough proxy until usage_metadata is wired).
    """
    inp = int(input_tokens or 0)
    out = int(output_tokens or 0)
    if inp == 0 and out == 0:
        tt = int(total_tokens or 0)
        if tt > 0:
            inp = (tt * 3) // 4
            out = tt - inp
    if inp == 0 and out == 0:
        return None
    rates = _per_million_rates_for_model(model_id)
    if rates is None:
        return None
    rin, rout = rates
    usd = (inp / 1_000_000.0) * rin + (out / 1_000_000.0) * rout
    return round(usd, 6)
