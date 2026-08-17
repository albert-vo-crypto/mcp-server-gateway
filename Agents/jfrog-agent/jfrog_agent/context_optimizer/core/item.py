"""
ContextItem — the canonical, framework-agnostic message used throughout
agent.context_optimizer.

Every adapter (LangChain, OpenAI, Anthropic, plain dict, ...) converts its
native message type to/from a list of ContextItem. Every layer operates only
on lists of ContextItem. This is what makes the framework portable.

Design rules:
  - One ContextItem == one message. No nested lists, no framework-specific
    content blocks. Multimodal blocks (images, etc.) are flattened to text
    by adapters or left in `metadata["original_content"]` for round-trip.
  - `pinned=True` items MUST be preserved by every layer that removes items.
    System prompts and the most recent user turn are pinned by adapters.
  - `tool_call_id` links a `role="tool"` item back to the `role="assistant"`
    item that issued the call. Layers MUST keep both ends of a pair or drop
    both. See `core.pipeline._preserve_tool_pairs`.
  - `score`, `cluster_id`, `metadata` are read/write scratch space for
    layers — `Pipeline.run` does not interpret them itself.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class ContextItem:
    role: Role
    content: str
    tool_call_id: Optional[str] = None       # role="tool" → assistant call id
    tool_calls: Optional[List[Dict[str, Any]]] = None  # role="assistant" calls
    name: Optional[str] = None               # tool name on role="tool"
    pinned: bool = False                     # never-drop flag for selection layers
    score: float = 0.0                       # set by rerankers, read by selectors
    cluster_id: Optional[int] = None         # set by clusterers
    metadata: Dict[str, Any] = field(default_factory=dict)

    # ------- convenience predicates -------

    @property
    def is_tool_response(self) -> bool:
        return self.role == "tool"

    @property
    def has_tool_calls(self) -> bool:
        return self.role == "assistant" and bool(self.tool_calls)

    @property
    def issued_tool_call_ids(self) -> List[str]:
        if not self.has_tool_calls:
            return []
        out = []
        for tc in self.tool_calls or []:
            tid = tc.get("id")
            if tid:
                out.append(tid)
        return out

    # ------- immutable helpers (avoid mutating originals in-place) -------

    def with_content(self, content: str) -> "ContextItem":
        new = copy.copy(self)
        new.content = content
        new.metadata = dict(self.metadata)
        return new

    def with_score(self, score: float) -> "ContextItem":
        new = copy.copy(self)
        new.score = score
        new.metadata = dict(self.metadata)
        return new

    def with_metadata(self, **kv: Any) -> "ContextItem":
        new = copy.copy(self)
        new.metadata = {**self.metadata, **kv}
        return new
