"""
OpenAI Chat-Completions ↔ ContextItem adapter.

Handles the message shapes accepted by ``client.chat.completions.create``:

    {"role": "system",    "content": "..."}
    {"role": "user",      "content": "..."}
    {"role": "assistant", "content": "...", "tool_calls": [...]}
    {"role": "tool",      "content": "...", "tool_call_id": "..."}

Multimodal content blocks (image_url, etc.) are flattened to a JSON string
on import and round-tripped via ``ContextItem.metadata['openai_content']``
when present.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from ..core.item import ContextItem

name = "openai"


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                else:
                    parts.append(json.dumps(block, default=str))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return str(content)


def to_items(messages: List[Dict[str, Any]]) -> List[ContextItem]:
    items: List[ContextItem] = []
    last_user_idx = -1
    for m in messages:
        role = m.get("role") or "user"
        meta: Dict[str, Any] = {}
        if isinstance(m.get("content"), list):
            meta["openai_content"] = m["content"]
        if role == "system":
            items.append(ContextItem(
                role="system",
                content=_content_to_text(m.get("content")),
                pinned=True,
                metadata=meta,
            ))
        elif role == "user":
            items.append(ContextItem(
                role="user",
                content=_content_to_text(m.get("content")),
                metadata=meta,
            ))
            last_user_idx = len(items) - 1
        elif role == "assistant":
            items.append(ContextItem(
                role="assistant",
                content=_content_to_text(m.get("content")),
                tool_calls=m.get("tool_calls") or None,
                metadata=meta,
            ))
        elif role == "tool":
            items.append(ContextItem(
                role="tool",
                content=_content_to_text(m.get("content")),
                tool_call_id=m.get("tool_call_id"),
                name=m.get("name"),
                metadata=meta,
            ))
        else:
            # function role (legacy) or unknown — treat as assistant text
            items.append(ContextItem(
                role="assistant",
                content=_content_to_text(m.get("content")),
                metadata={"original_role": role, **meta},
            ))
    if last_user_idx >= 0:
        items[last_user_idx].pinned = True
    return items


def from_items(items: List[ContextItem]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for it in items:
        msg: Dict[str, Any] = {"role": it.role}
        original = it.metadata.get("openai_content")
        msg["content"] = original if isinstance(original, list) else it.content
        if it.has_tool_calls:
            msg["tool_calls"] = it.tool_calls
        if it.is_tool_response:
            if it.tool_call_id:
                msg["tool_call_id"] = it.tool_call_id
            if it.name:
                msg["name"] = it.name
        out.append(msg)
    return out
