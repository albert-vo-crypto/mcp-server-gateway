"""
The four funnel layers (matches the "From chaos to clarity" diagram):

    Compression  -> squeeze content inside individual items
    Reranking    -> assign a relevance score to each item
    Selection    -> pick which items to keep within budget
    Clustering   -> group / dedupe near-identical items

Each module exposes one or more Layer subclasses. They can be combined in
any order via Pipeline; the standard funnel is Compression → Reranking →
Selection → Clustering, but plenty of pipelines skip layers entirely
(e.g. MINIMAL = Compression + Selection).
"""

from .clustering import NearDuplicateClusterer, RepeatedToolCallMerger
from .compression import (
    MessageDedup,
    OversizedContentCompression,
    PromptSummarizer,
    ToolResultCompression,
)
from .reranking import BM25Ranker, CompositeRanker, RecencyRanker
from .selection import SlidingWindowSelector, TokenBudgetSelector, TopKSelector

__all__ = [
    # compression
    "ToolResultCompression",
    "OversizedContentCompression",
    "MessageDedup",
    "PromptSummarizer",
    # reranking
    "BM25Ranker",
    "RecencyRanker",
    "CompositeRanker",
    # selection
    "TokenBudgetSelector",
    "SlidingWindowSelector",
    "TopKSelector",
    # clustering
    "NearDuplicateClusterer",
    "RepeatedToolCallMerger",
]
