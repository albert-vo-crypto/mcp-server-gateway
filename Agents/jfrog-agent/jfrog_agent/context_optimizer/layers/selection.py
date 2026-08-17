"""
Selection layer — picks which items to keep. All implementations honour:
  * pinned items are kept unconditionally (Pipeline also repairs invariants)
  * tool_call ↔ tool_response pairs stay together

Implementations:
    TokenBudgetSelector  keep highest-scoring items until budget hit
    SlidingWindowSelector  keep the last N non-pinned items + all pinned
    TopKSelector         keep top-K by score (used after a Reranking layer)
"""

from __future__ import annotations

from typing import List

from ..core.item import ContextItem
from ..core.pipeline import Layer, OptimizationContext
from ..core.tokens import count_item_tokens, count_items_tokens


# Helpers ------------------------------------------------------------------

def _expand_to_keep_pairs(
    keep: set[int], items: List[ContextItem]
) -> set[int]:
    """Add the index of any partnered tool_call / tool_response."""
    pos = {id(it): i for i, it in enumerate(items)}
    call_id_to_assistant_idx: dict[str, int] = {}
    tool_resp_idx_by_call_id: dict[str, int] = {}
    for i, it in enumerate(items):
        for cid in it.issued_tool_call_ids:
            call_id_to_assistant_idx[cid] = i
        if it.is_tool_response and it.tool_call_id:
            tool_resp_idx_by_call_id[it.tool_call_id] = i

    expanded = set(keep)
    changed = True
    while changed:
        changed = False
        for idx in list(expanded):
            it = items[idx]
            for cid in it.issued_tool_call_ids:
                pid = tool_resp_idx_by_call_id.get(cid)
                if pid is not None and pid not in expanded:
                    expanded.add(pid); changed = True
            if it.is_tool_response and it.tool_call_id:
                pid = call_id_to_assistant_idx.get(it.tool_call_id)
                if pid is not None and pid not in expanded:
                    expanded.add(pid); changed = True
    _ = pos
    return expanded


# Selectors ----------------------------------------------------------------


class TokenBudgetSelector(Layer):
    """
    Keep items in descending ``score`` order, accumulating until the running
    token total would exceed ``ctx.max_tokens``. Always keeps pinned items
    (treated as score = +inf). Final list is reordered to the original
    sequence to preserve conversation flow.

    Use this after a Reranking layer. If the ranker isn't run, all items
    have score 0.0 and ties break on original order, which is equivalent
    to a recency-from-top selector — usually not what you want.
    """

    name = "selection:token_budget"

    def __init__(self, *, max_tokens: int | None = None):
        self.max_tokens = max_tokens  # None = use ctx.max_tokens

    def apply(
        self, items: List[ContextItem], ctx: OptimizationContext
    ) -> List[ContextItem]:
        if not items:
            return items
        budget = self.max_tokens if self.max_tokens is not None else ctx.max_tokens
        original_idx = {id(it): i for i, it in enumerate(items)}

        # rank: pinned first (in their original order), then by score desc, then by recency
        def sort_key(it: ContextItem):
            return (
                0 if it.pinned else 1,
                -it.score,
                -original_idx[id(it)],
            )
        ordered = sorted(items, key=sort_key)

        kept_ids: set[int] = set()
        used = 0
        for it in ordered:
            cost = count_item_tokens(it, ctx.model)
            if it.pinned or used + cost <= budget:
                kept_ids.add(original_idx[id(it)])
                used += cost
            else:
                # If we're already over because of pinned alone, accept and stop
                if used > budget:
                    break

        kept_ids = _expand_to_keep_pairs(kept_ids, items)
        return [items[i] for i in sorted(kept_ids)]


class SlidingWindowSelector(Layer):
    """
    Keep the last ``window`` non-pinned items plus all pinned items.

    The simplest, cheapest selector — no scoring needed. Useful as a fall-back
    when no Reranker has been run, or as the only selection layer in a
    cost-conscious pipeline.
    """

    name = "selection:sliding_window"

    def __init__(self, *, window: int = 10):
        self.window = max(0, window)

    def apply(
        self, items: List[ContextItem], ctx: OptimizationContext
    ) -> List[ContextItem]:
        if not items:
            return items
        pinned_idxs = {i for i, it in enumerate(items) if it.pinned}
        non_pinned_idxs = [i for i, it in enumerate(items) if not it.pinned]
        keep_idxs = set(pinned_idxs)
        keep_idxs.update(non_pinned_idxs[-self.window :])

        keep_idxs = _expand_to_keep_pairs(keep_idxs, items)

        kept = [items[i] for i in sorted(keep_idxs)]
        # Optional secondary shrink if we somehow exceed budget
        while count_items_tokens(kept, ctx.model) > ctx.max_tokens and len(kept) > 1:
            for j, it in enumerate(kept):
                if not it.pinned:
                    del kept[j]
                    break
            else:
                break
        return kept


class TopKSelector(Layer):
    """
    Keep the top K items by score. Pinned items always survive. Tool-pair
    invariant is enforced by Pipeline.
    """

    name = "selection:top_k"

    def __init__(self, *, k: int = 20):
        self.k = max(0, k)

    def apply(
        self, items: List[ContextItem], ctx: OptimizationContext
    ) -> List[ContextItem]:
        if len(items) <= self.k:
            return items
        original_idx = {id(it): i for i, it in enumerate(items)}
        ranked = sorted(
            items,
            key=lambda it: (0 if it.pinned else 1, -it.score, original_idx[id(it)]),
        )
        keep_ids = {original_idx[id(it)] for it in ranked[: self.k]}
        keep_ids.update(original_idx[id(it)] for it in items if it.pinned)
        keep_ids = _expand_to_keep_pairs(keep_ids, items)
        return [items[i] for i in sorted(keep_ids)]
