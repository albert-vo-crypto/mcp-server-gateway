"""
Reranking layer — assigns a relevance score (``ContextItem.score``) to each
item. Does NOT remove items; that's Selection's job. Multiple rerankers can
be combined via CompositeRanker.

Implementations:
    BM25Ranker        lexical relevance vs. ctx.task (Okapi BM25, pure stdlib)
    RecencyRanker     exponential recency boost (most recent = 1.0)
    CompositeRanker   weighted combination of multiple rerankers
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import List, Sequence, Tuple

from ..core.item import ContextItem
from ..core.pipeline import Layer, OptimizationContext

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def _tokenize(text: str) -> List[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


def _bm25_scores(
    docs: List[List[str]],
    query: List[str],
    *,
    k1: float = 1.5,
    b: float = 0.75,
) -> List[float]:
    if not docs:
        return []
    n = len(docs)
    avgdl = sum(len(d) for d in docs) / max(1, n)
    df: Counter = Counter()
    for d in docs:
        for term in set(d):
            df[term] += 1
    idf = {
        term: math.log(1 + (n - dfi + 0.5) / (dfi + 0.5))
        for term, dfi in df.items()
    }
    out: List[float] = []
    for d in docs:
        if not d:
            out.append(0.0)
            continue
        tf = Counter(d)
        dl = len(d)
        s = 0.0
        for q in query:
            if q not in tf:
                continue
            denom = tf[q] + k1 * (1 - b + b * dl / max(1, avgdl))
            s += idf.get(q, 0.0) * (tf[q] * (k1 + 1)) / max(1e-9, denom)
        out.append(s)
    return out


def _infer_task(items: List[ContextItem]) -> str:
    last_user = ""
    for it in items:
        if it.role == "user":
            last_user = it.content
    return last_user


def _normalise(scores: List[float]) -> List[float]:
    if not scores:
        return scores
    lo, hi = min(scores), max(scores)
    if hi == lo:
        return [0.5 for _ in scores]
    return [(s - lo) / (hi - lo) for s in scores]


class BM25Ranker(Layer):
    """
    Score every item by BM25 relevance to ``ctx.task`` (or, if absent, the
    last user message). Scores are normalised to [0, 1].

    Pinned items get score = 1.0 so they sort first in any Selection layer.
    Items with no query overlap get score 0.0.
    """

    name = "reranking:bm25"

    def apply(
        self, items: List[ContextItem], ctx: OptimizationContext
    ) -> List[ContextItem]:
        if not items:
            return items
        task_text = ctx.task or _infer_task(items)
        q = _tokenize(task_text)
        if not q:
            return items
        docs = [_tokenize(it.content) for it in items]
        raw = _bm25_scores(docs, q)
        scores = _normalise(raw)
        for it, s in zip(items, scores):
            it.score = 1.0 if it.pinned else s
            it.metadata["bm25_raw"] = raw[items.index(it)] if it in items else 0.0
        return items


class RecencyRanker(Layer):
    """
    Exponential recency boost. Most-recent item gets 1.0; the item ``half_life``
    positions back gets 0.5; etc.

    Useful as a stand-alone scorer when there's no task, or as a tiebreaker
    inside a CompositeRanker.
    """

    name = "reranking:recency"

    def __init__(self, *, half_life: int = 5):
        self.half_life = max(1, half_life)

    def apply(
        self, items: List[ContextItem], ctx: OptimizationContext
    ) -> List[ContextItem]:
        if not items:
            return items
        n = len(items)
        for i, it in enumerate(items):
            dist = (n - 1) - i  # 0 = most recent
            it.score = 1.0 if it.pinned else (0.5 ** (dist / self.half_life))
        return items


class CompositeRanker(Layer):
    """
    Combine multiple rerankers with weights, then write the weighted sum back
    to ``item.score``. Each child ranker is run in order, its score read,
    and its score is then replaced.

    Example::

        CompositeRanker([(BM25Ranker(), 0.7), (RecencyRanker(half_life=4), 0.3)])
    """

    name = "reranking:composite"

    def __init__(self, parts: Sequence[Tuple[Layer, float]]):
        if not parts:
            raise ValueError("CompositeRanker needs at least one (layer, weight) pair")
        self.parts = list(parts)

    def apply(
        self, items: List[ContextItem], ctx: OptimizationContext
    ) -> List[ContextItem]:
        accum = [0.0] * len(items)
        total_w = sum(w for _, w in self.parts) or 1.0
        for layer, weight in self.parts:
            layer.apply(items, ctx)
            for i, it in enumerate(items):
                accum[i] += it.score * (weight / total_w)
        for it, s in zip(items, accum):
            it.score = 1.0 if it.pinned else s
        return items
