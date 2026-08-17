"""
Ready-to-use Pipelines following the funnel image
    Compression -> Reranking -> Selection -> Clustering

Pick one based on your cost/quality tradeoff:

    MINIMAL    cheapest, no LLM, no ranking — for very short conversations
    BALANCED   default for most agents — BM25 + recency + token-budget
    AGGRESSIVE full funnel — adds clustering + (optionally) summarizer

Or build your own by composing layers directly::

    from agent.context_optimizer import Pipeline
    from agent.context_optimizer.layers import (
        ToolResultCompression, MessageDedup, BM25Ranker, TokenBudgetSelector,
        NearDuplicateClusterer, RepeatedToolCallMerger,
    )
    my_pipeline = Pipeline([
        ToolResultCompression(max_chars=1500),
        MessageDedup(),
        RepeatedToolCallMerger(),
        BM25Ranker(),
        TokenBudgetSelector(),
        NearDuplicateClusterer(threshold=0.9),
    ], model="gpt-4o-mini", max_tokens=6000)
"""

from __future__ import annotations

from typing import Callable, List, Optional

from .core.pipeline import Pipeline
from .layers.clustering import NearDuplicateClusterer, RepeatedToolCallMerger
from .layers.compression import (
    MessageDedup,
    OversizedContentCompression,
    PromptSummarizer,
    ToolResultCompression,
)
from .layers.reranking import BM25Ranker, CompositeRanker, RecencyRanker
from .layers.selection import SlidingWindowSelector, TokenBudgetSelector


def minimal(*, model: str = "gpt-4o-mini", max_tokens: int = 8_000) -> Pipeline:
    """No LLM, no ranking. Compression + sliding-window only."""
    return Pipeline(
        [
            ToolResultCompression(max_chars=4_000, head_rows=20, tail_rows=5),
            MessageDedup(),
            SlidingWindowSelector(window=12),
        ],
        model=model, max_tokens=max_tokens, name="minimal",
    )


def balanced(*, model: str = "gpt-4o-mini", max_tokens: int = 8_000) -> Pipeline:
    """
    Default for production agents. Adds dedup + BM25-relevance ordering +
    token-budget pruning. No LLM call inside the optimizer. Pure stdlib.
    """
    return Pipeline(
        [
            OversizedContentCompression(max_chars=2_000, head_rows=15, tail_rows=5),
            MessageDedup(),
            RepeatedToolCallMerger(),
            ToolResultCompression(max_chars=2_000, head_rows=15, tail_rows=5),
            CompositeRanker([
                (BM25Ranker(), 0.7),
                (RecencyRanker(half_life=5), 0.3),
            ]),
            TokenBudgetSelector(),
        ],
        model=model, max_tokens=max_tokens, name="balanced",
    )


def aggressive(
    *,
    model: str = "gpt-4o-mini",
    max_tokens: int = 4_000,
    summarizer: Optional[Callable] = None,
) -> Pipeline:
    """
    Full funnel. Adds near-duplicate clustering. If a `summarizer` callable
    is supplied (``fn(items, instruction) -> str``), folds older history
    into a single summary item before selection.
    """
    layers: List = [
        MessageDedup(),
        RepeatedToolCallMerger(),
        ToolResultCompression(max_chars=1_000, head_rows=10, tail_rows=3),
        NearDuplicateClusterer(threshold=0.85),
    ]
    if summarizer is not None:
        layers.append(PromptSummarizer(summarizer, keep_recent=6, trigger_threshold=0.7))
    layers += [
        CompositeRanker([
            (BM25Ranker(), 0.6),
            (RecencyRanker(half_life=4), 0.4),
        ]),
        TokenBudgetSelector(),
    ]
    return Pipeline(layers, model=model, max_tokens=max_tokens, name="aggressive")
