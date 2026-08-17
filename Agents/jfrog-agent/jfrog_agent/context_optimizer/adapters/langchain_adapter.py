"""
LangChain ↔ ContextItem adapter.

Maps SystemMessage / HumanMessage / AIMessage / ToolMessage to ContextItem
and back. The conversion is lossless for everything the optimizer touches
(role, content, tool_call_id, tool_calls). Anything else (additional_kwargs,
response_metadata) is stashed in ``ContextItem.metadata['lc_extras']`` so it
survives the round trip if downstream code still cares.

Pinning policy:
  * All SystemMessages are pinned (always kept).
  * The most-recent HumanMessage is pinned (the current user turn).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from ..core.item import ContextItem

name = "langchain"


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text") or block.get("content")
                parts.append(text if isinstance(text, str)
                             else json.dumps(block, default=str))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return str(content)


def to_items(messages: List[Any]) -> List[ContextItem]:
    """Convert a list of LangChain BaseMessage to ContextItems."""
    from langchain_core.messages import (
        AIMessage,
        HumanMessage,
        SystemMessage,
        ToolMessage,
    )

    items: List[ContextItem] = []
    last_human_idx: int = -1
    for m in messages:
        if isinstance(m, SystemMessage):
            it = ContextItem(
                role="system",
                content=_content_to_text(m.content),
                pinned=True,
                metadata={"lc_extras": _stash_extras(m)},
            )
        elif isinstance(m, HumanMessage):
            content = _content_to_text(m.content)
            it = ContextItem(
                role="user",
                content=content,
                pinned=content.strip().startswith("Request:"),
                metadata={"lc_extras": _stash_extras(m)},
            )
            last_human_idx = len(items)
        elif isinstance(m, AIMessage):
            tcs = getattr(m, "tool_calls", None) or None
            it = ContextItem(
                role="assistant",
                content=_content_to_text(m.content),
                tool_calls=tcs,
                metadata={"lc_extras": _stash_extras(m)},
            )
        elif isinstance(m, ToolMessage):
            it = ContextItem(
                role="tool",
                content=_content_to_text(m.content),
                tool_call_id=getattr(m, "tool_call_id", None) or None,
                name=getattr(m, "name", None),
                metadata={"lc_extras": _stash_extras(m)},
            )
        else:
            # Unknown subclass — treat as assistant text to keep things safe.
            it = ContextItem(
                role="assistant",
                content=_content_to_text(getattr(m, "content", str(m))),
                metadata={"lc_extras": {"unknown_type": type(m).__name__}},
            )
        items.append(it)

    if last_human_idx >= 0:
        content = items[last_human_idx].content or ""
        # JFrog summarize packs huge findings JSON into the last HumanMessage;
        # leave it unpinned so compression/selection can trim it.
        if "Findings (JSON)" not in content and len(content) <= 2000:
            items[last_human_idx].pinned = True
    return items


def from_items(items: List[ContextItem]) -> List[Any]:
    """Convert ContextItems back to LangChain BaseMessage objects."""
    from langchain_core.messages import (
        AIMessage,
        HumanMessage,
        SystemMessage,
        ToolMessage,
    )

    out: List[Any] = []
    for it in items:
        extras = it.metadata.get("lc_extras") or {}
        kwargs: Dict[str, Any] = {}
        if "additional_kwargs" in extras:
            kwargs["additional_kwargs"] = extras["additional_kwargs"]
        if it.role == "system":
            out.append(SystemMessage(content=it.content, **kwargs))
        elif it.role == "user":
            out.append(HumanMessage(content=it.content, **kwargs))
        elif it.role == "assistant":
            msg_kwargs = dict(kwargs)
            if it.tool_calls:
                msg_kwargs["tool_calls"] = it.tool_calls
            out.append(AIMessage(content=it.content, **msg_kwargs))
        elif it.role == "tool":
            out.append(
                ToolMessage(
                    content=it.content,
                    tool_call_id=it.tool_call_id or "",
                    name=it.name,
                    **kwargs,
                )
            )
    return out


def _stash_extras(msg: Any) -> Dict[str, Any]:
    extras: Dict[str, Any] = {}
    addl = getattr(msg, "additional_kwargs", None)
    if addl:
        extras["additional_kwargs"] = addl
    return extras
