"""
Layer, Pipeline, and result types — the funnel runner.

The mental model matches the image:
    raw items -> Compression -> Reranking -> Selection -> Clustering -> final items

Each Layer is a self-contained transform with one method:
    def apply(self, items, ctx) -> list[ContextItem]

Pipeline.run wires them together, captures per-layer metrics, and enforces
two invariants that every layer is expected to respect:
    1. pinned items survive every selection/clustering layer
    2. assistant tool_call + matching tool response stay together (or both leave)

If a layer violates these invariants, Pipeline will repair the output before
handing it to the next layer (rather than crashing).
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..pricing import input_usd_per_million
from .item import ContextItem
from .tokens import count_items_tokens

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Context passed to every layer
# ---------------------------------------------------------------------------


@dataclass
class OptimizationContext:
    """Shared, read-only context handed to every layer in a single run."""

    task: Optional[str] = None
    """The current user goal/question. Rerankers use this to score items."""

    model: str = "gpt-4o-mini"
    """Model the optimized messages will be sent to. Drives token counting + pricing."""

    max_tokens: int = 8_000
    """Target token budget. Selection layers should respect this."""

    extras: Dict[str, Any] = field(default_factory=dict)
    """Free-form bag for caller-specific knobs (e.g. embedding_fn, summarizer)."""


# ---------------------------------------------------------------------------
# Per-layer / per-run result types
# ---------------------------------------------------------------------------


@dataclass
class LayerResult:
    layer_name: str
    items_before: int
    items_after: int
    tokens_before: int
    tokens_after: int
    elapsed_ms: float
    extras: Dict[str, Any] = field(default_factory=dict)

    @property
    def saved_tokens(self) -> int:
        return max(0, self.tokens_before - self.tokens_after)

    @property
    def dropped_items(self) -> int:
        return max(0, self.items_before - self.items_after)


@dataclass
class PipelineResult:
    items: List[ContextItem]
    layers: List[LayerResult]
    tokens_before: int
    tokens_after: int
    model: str
    elapsed_ms: float

    @property
    def saved_tokens(self) -> int:
        return max(0, self.tokens_before - self.tokens_after)

    @property
    def compression_ratio(self) -> float:
        return (self.tokens_after / self.tokens_before) if self.tokens_before else 1.0

    @property
    def saved_usd(self) -> Optional[float]:
        rate = input_usd_per_million(self.model)
        if rate is None:
            return None
        return (self.saved_tokens / 1_000_000) * rate

    def summary(self) -> str:
        usd_str = f" (~${self.saved_usd:.6f})" if self.saved_usd is not None else ""
        lines = [
            f"context_optimizer pipeline: "
            f"{self.tokens_before} -> {self.tokens_after} tokens "
            f"(saved {self.saved_tokens}{usd_str}, "
            f"ratio {self.compression_ratio:.2f}) in {self.elapsed_ms:.1f}ms"
        ]
        for lr in self.layers:
            lines.append(
                f"  [{lr.layer_name:>22s}] "
                f"items {lr.items_before:>3d}->{lr.items_after:<3d}  "
                f"tokens {lr.tokens_before:>5d}->{lr.tokens_after:<5d}  "
                f"({lr.elapsed_ms:5.1f}ms)"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Layer ABC + Pipeline runner
# ---------------------------------------------------------------------------


class Layer(ABC):
    """
    Base class for every layer in the funnel.

    Implementations must override `apply` and either set a class-level `name`
    or override `self.name` in __init__.
    """

    name: str = "layer"

    @abstractmethod
    def apply(
        self, items: List[ContextItem], ctx: OptimizationContext
    ) -> List[ContextItem]:
        """Transform the item list. May add, remove, or rewrite items."""
        raise NotImplementedError


class Pipeline:
    """
    Ordered list of layers, run sequentially.

    Usage::

        from agent.context_optimizer import Pipeline, presets
        from agent.context_optimizer.adapters import langchain_adapter as lc

        items = lc.to_items(state["messages"])
        result = presets.BALANCED.run(items, task=state["user_query"])
        new_messages = lc.from_items(result.items)
    """

    def __init__(
        self,
        layers: List[Layer],
        *,
        model: str = "gpt-4o-mini",
        max_tokens: int = 8_000,
        name: str = "pipeline",
    ):
        self.layers = layers
        self.model = model
        self.max_tokens = max_tokens
        self.name = name

    def run(
        self,
        items: List[ContextItem],
        *,
        task: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        extras: Optional[Dict[str, Any]] = None,
    ) -> PipelineResult:
        ctx = OptimizationContext(
            task=task,
            model=model or self.model,
            max_tokens=max_tokens if max_tokens is not None else self.max_tokens,
            extras=dict(extras or {}),
        )
        pipeline_start = time.perf_counter()
        tokens_before = count_items_tokens(items, ctx.model)
        # Stamp a stable original-position on every input item so that layers
        # which return NEW objects (e.g. ToolResultCompression returning
        # ``it.with_content(...)``) can still be ordered correctly. Without
        # this, ``id(it)`` lookups silently fall through to "unknown" and the
        # item ends up at the tail of the list — which has shipped the
        # infamous "tool_call_ids did not have response messages" bug.
        for i, it in enumerate(items):
            it.metadata.setdefault("_ctxopt_pos", i)
        current = list(items)
        layer_results: List[LayerResult] = []

        for layer in self.layers:
            layer_start = time.perf_counter()
            items_before = len(current)
            tokens_before_layer = count_items_tokens(current, ctx.model)
            try:
                next_items = layer.apply(current, ctx)
            except Exception as exc:
                log.warning(
                    "context_optimizer: layer %r failed (%s); skipping",
                    layer.name, exc,
                )
                next_items = current
            next_items = _preserve_invariants(current, next_items)
            tokens_after_layer = count_items_tokens(next_items, ctx.model)
            layer_results.append(
                LayerResult(
                    layer_name=layer.name,
                    items_before=items_before,
                    items_after=len(next_items),
                    tokens_before=tokens_before_layer,
                    tokens_after=tokens_after_layer,
                    elapsed_ms=(time.perf_counter() - layer_start) * 1000.0,
                )
            )
            current = next_items

        # Final safety pass: guarantee the output is LLM-acceptable even
        # if the input was malformed or a layer misbehaved. Drops any
        # assistant tool_calls whose response isn't in the output, and any
        # tool responses whose assistant tool_call isn't in the output.
        # This is the optimizer's contract to its caller.
        current = _final_safety_cleanup(current)
        tokens_after = count_items_tokens(current, ctx.model)
        return PipelineResult(
            items=current,
            layers=layer_results,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            model=ctx.model,
            elapsed_ms=(time.perf_counter() - pipeline_start) * 1000.0,
        )


# ---------------------------------------------------------------------------
# Invariant repair (pinned + tool pair)
# ---------------------------------------------------------------------------


def _preserve_invariants(
    before: List[ContextItem], after: List[ContextItem]
) -> List[ContextItem]:
    """
    Re-add any pinned items the layer dropped, and re-add any tool_call/
    tool_response partners that were broken. Order follows `before`.
    """
    kept_ids = {id(it) for it in after}
    repaired = list(after)

    # 1. re-attach pinned items the layer removed
    for it in before:
        if it.pinned and id(it) not in kept_ids:
            repaired.append(it)
            kept_ids.add(id(it))

    # 2. re-attach broken tool pairs
    call_id_to_assistant = {
        cid: it
        for it in before
        for cid in it.issued_tool_call_ids
    }
    tool_resp_by_call_id = {
        it.tool_call_id: it
        for it in before
        if it.is_tool_response and it.tool_call_id
    }

    in_after_call_ids = {
        cid for it in repaired for cid in it.issued_tool_call_ids
    }
    in_after_tool_resp_ids = {
        it.tool_call_id for it in repaired if it.is_tool_response and it.tool_call_id
    }
    # assistant-call kept but tool-response dropped -> re-add response
    for cid in in_after_call_ids:
        if cid not in in_after_tool_resp_ids and cid in tool_resp_by_call_id:
            partner = tool_resp_by_call_id[cid]
            if id(partner) not in kept_ids:
                repaired.append(partner)
                kept_ids.add(id(partner))
    # tool-response kept but assistant-call dropped -> re-add call
    for cid in in_after_tool_resp_ids:
        if cid not in in_after_call_ids and cid in call_id_to_assistant:
            partner = call_id_to_assistant[cid]
            if id(partner) not in kept_ids:
                repaired.append(partner)
                kept_ids.add(id(partner))

    # 3. reorder repaired list to match the order they appear in `before`.
    # Use a stable position stamped on ``metadata['_ctxopt_pos']`` by
    # Pipeline.run, falling back to id() lookup for items that predate
    # the stamping (e.g. items synthesized by PromptSummarizer). This is
    # critical because layers like ToolResultCompression return NEW
    # objects via ``with_content(...)`` — those new objects share the
    # original's ``_ctxopt_pos`` but have a different ``id()``.
    id_order = {id(it): i for i, it in enumerate(before)}

    def _pos(it: ContextItem) -> int:
        p = it.metadata.get("_ctxopt_pos") if it.metadata else None
        if isinstance(p, int):
            return p
        return id_order.get(id(it), len(before) + len(repaired))

    repaired.sort(key=_pos)
    return repaired


def _final_safety_cleanup(items: List[ContextItem]) -> List[ContextItem]:
    """
    Last-mile guarantee: the returned list is always a valid LLM chat input.

    Drops any item that would cause an OpenAI 400:
      * assistant message with a tool_call whose response is missing
      * tool response whose assistant tool_call is missing

    Repair (``_preserve_invariants``) tries to restore missing partners by
    pulling them from the layer's input. But if BOTH ends of a pair never
    existed in the input, or if upstream malformed the input itself, the
    only safe choice is to drop the dangling half. This is the optimizer's
    final contract to the caller — never produce a sequence that crashes
    the LLM API.

    For assistants with multiple tool_calls where SOME responses are missing,
    we drop the whole assistant (and any sibling responses) — partial
    tool_call lists also crash the API.
    """
    if not items:
        return items
    tool_resp_call_ids = {
        it.tool_call_id for it in items if it.is_tool_response and it.tool_call_id
    }
    # Pass 1: drop assistants that have ANY orphan tool_call
    cleaned: List[ContextItem] = []
    valid_assistant_call_ids: set[str] = set()
    for it in items:
        if it.has_tool_calls:
            issued = set(it.issued_tool_call_ids)
            if not issued.issubset(tool_resp_call_ids):
                continue  # drop: at least one tool_call has no response
            valid_assistant_call_ids.update(issued)
        cleaned.append(it)
    # Pass 2: drop tool responses whose assistant was dropped
    final: List[ContextItem] = []
    for it in cleaned:
        if it.is_tool_response and it.tool_call_id:
            if it.tool_call_id not in valid_assistant_call_ids:
                continue
        final.append(it)
    return final
