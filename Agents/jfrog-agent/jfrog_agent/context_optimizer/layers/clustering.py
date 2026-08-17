"""
Clustering layer — groups near-duplicate items and merges them. Useful when
an agent loops or repeats itself (very common with retry-prone tool calls).

Implementations:
    NearDuplicateClusterer  shingle-based Jaccard similarity, merges items
                            above the threshold into the most recent one
    RepeatedToolCallMerger  collapse N identical tool_call+tool_response
                            pairs into a single representative pair with
                            a "[repeated N times]" prefix on the response
"""

from __future__ import annotations

import json
import re
from typing import List

from ..core.item import ContextItem
from ..core.pipeline import Layer, OptimizationContext

_SHINGLE_RE = re.compile(r"\w+")


def _shingles(text: str, k: int = 3) -> set[str]:
    toks = _SHINGLE_RE.findall((text or "").lower())
    if len(toks) < k:
        return set(toks)
    return {" ".join(toks[i : i + k]) for i in range(len(toks) - k + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class NearDuplicateClusterer(Layer):
    """
    Group items whose content has Jaccard(shingle_k=3) similarity ≥ threshold.
    Within each cluster, keep only the most recent member; older members
    are dropped. Pinned items are kept and serve as cluster anchors but
    never get merged away.

    Skips ``role="assistant"`` items with tool_calls (their identity is the
    call, not the text) and ``role="tool"`` items (handled by
    RepeatedToolCallMerger).
    """

    name = "clustering:near_duplicate"

    def __init__(self, *, threshold: float = 0.85, k: int = 3):
        self.threshold = threshold
        self.k = k

    def apply(
        self, items: List[ContextItem], ctx: OptimizationContext
    ) -> List[ContextItem]:
        if not items:
            return items

        candidates = [
            (i, it) for i, it in enumerate(items)
            if it.role in {"user", "assistant"}
            and not it.has_tool_calls
            and not it.is_tool_response
        ]
        if len(candidates) < 2:
            return items

        # Compute shingles once
        shingles = {i: _shingles(it.content, self.k) for i, it in candidates}

        # Greedy clustering: walk newest -> oldest; each item joins the
        # newest existing cluster it's similar to, otherwise starts its own.
        cluster_anchor: dict[int, int] = {}  # idx -> anchor idx (newer)
        anchors: List[int] = []  # in newest-first order
        for i, it in reversed(candidates):
            joined = False
            for anchor in anchors:
                if _jaccard(shingles[i], shingles[anchor]) >= self.threshold:
                    cluster_anchor[i] = anchor
                    it.cluster_id = anchor
                    joined = True
                    break
            if not joined:
                anchors.append(i)
                cluster_anchor[i] = i
                it.cluster_id = i

        # Drop non-pinned items that aren't their cluster's anchor
        out: List[ContextItem] = []
        for i, it in enumerate(items):
            if i in cluster_anchor and cluster_anchor[i] != i and not it.pinned:
                continue
            out.append(it)
        return out


class RepeatedToolCallMerger(Layer):
    """
    Detect repeated ``(assistant tool_call, tool response)`` pairs where the
    assistant called the same tool with the same arguments and the response
    body is identical. Keep one representative pair, drop the rest, and
    prefix the kept response with ``[repeated N times]``.

    This is what happens when an agent retries a flaky tool — without this
    layer the context fills up with carbon copies of the same call.
    """

    name = "clustering:repeated_tool_calls"

    def _call_fingerprint(self, item: ContextItem) -> str | None:
        if not item.has_tool_calls or not item.tool_calls:
            return None
        parts = []
        for tc in item.tool_calls:
            args = tc.get("args") or tc.get("arguments") or {}
            if isinstance(args, str):
                args_str = args
            else:
                args_str = json.dumps(args, sort_keys=True, default=str)
            parts.append(f"{tc.get('name', '')}::{args_str}")
        return "|".join(parts)

    def apply(
        self, items: List[ContextItem], ctx: OptimizationContext
    ) -> List[ContextItem]:
        if not items:
            return items

        # Build (assistant_idx, tool_response_idx) pairs
        call_id_to_tool_idx: dict[str, int] = {}
        for i, it in enumerate(items):
            if it.is_tool_response and it.tool_call_id:
                call_id_to_tool_idx[it.tool_call_id] = i
        pairs: List[tuple[int, int, str, str]] = []
        for i, it in enumerate(items):
            fp = self._call_fingerprint(it)
            if fp is None:
                continue
            for cid in it.issued_tool_call_ids:
                resp_idx = call_id_to_tool_idx.get(cid)
                if resp_idx is None:
                    continue
                resp = items[resp_idx]
                pairs.append((i, resp_idx, fp, resp.content or ""))

        # Group pairs by (fingerprint, response_content)
        groups: dict[tuple[str, str], List[tuple[int, int]]] = {}
        for ai_i, tool_i, fp, resp in pairs:
            groups.setdefault((fp, resp), []).append((ai_i, tool_i))

        drop: set[int] = set()
        for (fp, resp), members in groups.items():
            if len(members) < 2:
                continue
            members.sort()
            keep_ai, keep_tool = members[-1]  # keep the most recent pair
            for ai_i, tool_i in members[:-1]:
                drop.add(ai_i)
                drop.add(tool_i)
            kept = items[keep_tool]
            items[keep_tool] = kept.with_content(
                f"[repeated {len(members)} times by RepeatedToolCallMerger]\n"
                + kept.content
            ).with_metadata(repeated_count=len(members))

        if not drop:
            return items
        return [it for i, it in enumerate(items) if i not in drop]
