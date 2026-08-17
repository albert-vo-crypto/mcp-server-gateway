"""
agent.context_optimizer
=======================

A framework-agnostic, layered context-window optimizer for LLM agents.

Mental model (matches the "From chaos to clarity, one layer at a time"
funnel image):

    raw messages
        │
        ▼
    [Compression]     squeeze content inside individual items
    [Reranking]       assign a relevance score to each item
    [Selection]       pick which items to keep within the token budget
    [Clustering]      group / dedupe near-identical items
        │
        ▼
    optimized messages

Three ways to use it:

1. ``presets`` — one-liner ready-made pipelines::

       from agent.context_optimizer import presets, adapters
       items = adapters.langchain_adapter.to_items(messages)
       result = presets.balanced().run(items, task="answer the user")
       messages = adapters.langchain_adapter.from_items(result.items)

2. Custom Pipeline — pick the layers you want::

       from agent.context_optimizer import Pipeline
       from agent.context_optimizer.layers import (
           ToolResultCompression, BM25Ranker, TokenBudgetSelector,
       )
       pipe = Pipeline([
           ToolResultCompression(max_chars=2000),
           BM25Ranker(),
           TokenBudgetSelector(),
       ], max_tokens=6000)
       result = pipe.run(items, task=user_q)

3. Backward-compat (this BigQuery agent) — ``get_optimizer()`` returns an
   object with ``.optimize(messages, task=) -> (messages, meta)`` so
   existing code in ``agent/nodes.py`` keeps working unchanged. The
   pipeline used is controlled by ``CTX_OPT_*`` env vars (see below).

Plug into a new agent framework: add an adapter under
``agent/context_optimizer/adapters/`` that exposes ``to_items`` and
``from_items``. Every layer keeps working unchanged.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable, List, Optional, Tuple

from . import adapters, presets
from .core.item import ContextItem, Role
from .core.pipeline import (
    Layer,
    LayerResult,
    OptimizationContext,
    Pipeline,
    PipelineResult,
)
from .core.tokens import count_item_tokens, count_items_tokens
from .layers import (
    BM25Ranker,
    CompositeRanker,
    MessageDedup,
    NearDuplicateClusterer,
    PromptSummarizer,
    RecencyRanker,
    RepeatedToolCallMerger,
    SlidingWindowSelector,
    TokenBudgetSelector,
    ToolResultCompression,
    TopKSelector,
)
from .pricing import INPUT_USD_PER_MILLION, input_usd_per_million, usd_for_tokens

log = logging.getLogger(__name__)

__all__ = [
    # core
    "ContextItem", "Role",
    "Layer", "LayerResult", "OptimizationContext",
    "Pipeline", "PipelineResult",
    "count_item_tokens", "count_items_tokens",
    # layers (re-exported for `from agent.context_optimizer import ...` UX)
    "ToolResultCompression", "MessageDedup", "PromptSummarizer",
    "BM25Ranker", "RecencyRanker", "CompositeRanker",
    "TokenBudgetSelector", "SlidingWindowSelector", "TopKSelector",
    "NearDuplicateClusterer", "RepeatedToolCallMerger",
    # adapters + presets + pricing
    "adapters", "presets",
    "INPUT_USD_PER_MILLION", "input_usd_per_million", "usd_for_tokens",
    # backward-compat for this agent
    "get_optimizer", "is_enabled", "build_summarizer_llm_call",
    "OptimizeMeta", "clear_optimizer_cache",
]


# ---------------------------------------------------------------------------
# Env-driven factory + backward-compat surface (this BigQuery agent)
# ---------------------------------------------------------------------------


def is_enabled() -> bool:
    """True iff ``CTX_OPT_ENABLED`` is set to a truthy value."""
    return os.environ.get("CTX_OPT_ENABLED", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _envstr(name: str, default: str) -> str:
    v = os.environ.get(name, "").strip()
    return v if v else default


def _envint(name: str, default: int) -> int:
    v = os.environ.get(name, "").strip()
    return int(v) if v else default


def build_summarizer_llm_call(
    *, model: str = "gpt-4o-mini", max_tokens: int = 400
) -> Callable[[List[ContextItem], str], str]:
    """
    Build a summarizer function compatible with ``PromptSummarizer`` using
    this agent's existing OpenAI/AI Gateway config. Imported lazily so the
    framework doesn't drag LangChain in unless this is actually called.
    """
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI

    import os

    from ..settings import settings

    api_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_API_BASE") or os.getenv("OPENAI_BASE_URL")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY required for context optimizer summarizer")

    kwargs: dict = {
        "model": model,
        "api_key": api_key,
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    if base_url:
        kwargs["base_url"] = base_url
    elif settings.llm_model:
        kwargs["model"] = model or settings.llm_model

    client = ChatOpenAI(**kwargs)

    def _call(items: List[ContextItem], instruction: str) -> str:
        joined = "\n".join(f"[{it.role}] {it.content}" for it in items)
        resp = client.invoke(
            [SystemMessage(content=instruction), HumanMessage(content=joined)]
        )
        return resp.content if isinstance(resp.content, str) else str(resp.content)

    return _call


@dataclass
class OptimizeMeta:
    """Backward-compat metadata for ``LegacyAdapter.optimize`` callers."""
    input_tokens: int
    output_tokens: int
    saved: int
    compression_ratio: float
    strategy_used: str
    tool_messages_compressed: int
    elapsed_ms: float
    saved_usd: Optional[float]
    layer_results: List[LayerResult]

    @property
    def messages_dropped(self) -> int:
        return sum(lr.dropped_items for lr in self.layer_results)


class LegacyAdapter:
    """
    Backward-compat wrapper so existing call sites (``agent/nodes.py``)
    continue to work without any change while we migrate them to the
    framework-agnostic ``Pipeline`` API.
    """

    def __init__(self, pipeline: Pipeline, *, preset_name: str = "balanced"):
        self.pipeline = pipeline
        self.preset_name = preset_name

    def optimize(
        self, messages: List[Any], *, task: Optional[str] = None
    ) -> Tuple[List[Any], OptimizeMeta]:
        items = adapters.langchain_adapter.to_items(messages)
        result = self.pipeline.run(items, task=task)
        out = adapters.langchain_adapter.from_items(result.items)
        tool_compressed = sum(
            1 for lr in result.layers
            if lr.layer_name.startswith("compression:tool_results")
            and lr.tokens_after < lr.tokens_before
        )
        return out, OptimizeMeta(
            input_tokens=result.tokens_before,
            output_tokens=result.tokens_after,
            saved=result.saved_tokens,
            compression_ratio=result.compression_ratio,
            strategy_used=self.preset_name,
            tool_messages_compressed=tool_compressed,
            elapsed_ms=result.elapsed_ms,
            saved_usd=result.saved_usd,
            layer_results=result.layers,
        )


def _build_pipeline_from_env() -> Tuple[Pipeline, str]:
    """
    Translate ``CTX_OPT_*`` env vars into a configured Pipeline.

    Env vars (all optional except ``CTX_OPT_ENABLED``):
        CTX_OPT_PRESET             "minimal" | "balanced" | "aggressive"
                                   default: "balanced"
        CTX_OPT_MODEL              model name for token counting / pricing
                                   default: "gpt-4o-mini"
        CTX_OPT_MAX_TOKENS         token budget (int) default: 8000
        CTX_OPT_USE_SUMMARIZER     truthy → only matters for "aggressive";
                                   when set, an LLM-based PromptSummarizer is
                                   added to the pipeline
        CTX_OPT_STRATEGY           legacy alias: maps "sliding-window"→minimal,
                                   "relevance"|"summarizer"|"hybrid"→balanced
                                   (kept so old .env files still work)
    """
    model = _envstr("CTX_OPT_MODEL", "gpt-4o-mini")
    max_tokens = _envint("CTX_OPT_MAX_TOKENS", 8_000)

    preset_name = _envstr("CTX_OPT_PRESET", "").lower()
    if not preset_name:
        legacy = _envstr("CTX_OPT_STRATEGY", "").lower()
        preset_name = {
            "sliding-window": "minimal",
            "relevance":      "balanced",
            "summarizer":     "balanced",
            "hybrid":         "aggressive",
        }.get(legacy, "balanced")

    if preset_name == "minimal":
        return presets.minimal(model=model, max_tokens=max_tokens), "minimal"
    if preset_name == "aggressive":
        summ = None
        if _envstr("CTX_OPT_USE_SUMMARIZER", "").lower() in {"1", "true", "yes", "on"}:
            try:
                summ = build_summarizer_llm_call(model=model)
            except Exception as exc:
                log.warning("context_optimizer: cannot build summarizer (%s); "
                            "running aggressive preset without it", exc)
        return presets.aggressive(model=model, max_tokens=max_tokens, summarizer=summ), "aggressive"
    return presets.balanced(model=model, max_tokens=max_tokens), "balanced"


@lru_cache(maxsize=1)
def get_optimizer() -> Optional[LegacyAdapter]:
    if not is_enabled():
        return None
    pipeline, preset_name = _build_pipeline_from_env()
    log.info("context_optimizer: enabled preset=%s model=%s max_tokens=%s",
             preset_name, pipeline.model, pipeline.max_tokens)
    return LegacyAdapter(pipeline, preset_name=preset_name)


def clear_optimizer_cache() -> None:
    """Clear cached optimizer (call after CTX_OPT_* env changes)."""
    get_optimizer.cache_clear()
