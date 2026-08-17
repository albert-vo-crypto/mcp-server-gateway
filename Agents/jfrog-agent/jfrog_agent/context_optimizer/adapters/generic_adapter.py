"""
Generic ``{"role", "content"}`` dict adapter.

Works for Anthropic Messages API, Bedrock Converse, vLLM, raw transformers,
and any custom in-house agent that stores chat history as a list of dicts.

Roles outside the {"system", "user", "assistant", "tool"} set are normalised:
  * "human"        → "user"
  * "ai" / "bot"   → "assistant"
  * everything else → "assistant" with the original role stashed in metadata
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..core.item import ContextItem

name = "generic"

_ROLE_ALIASES = {
    "human": "user",
    "ai": "assistant",
    "bot": "assistant",
    "model": "assistant",
    "function": "tool",
}


def _normalise_role(role: str) -> str:
    role = (role or "").strip().lower()
    if role in {"system", "user", "assistant", "tool"}:
        return role
    return _ROLE_ALIASES.get(role, "assistant")


def to_items(messages: List[Dict[str, Any]]) -> List[ContextItem]:
    items: List[ContextItem] = []
    last_user_idx = -1
    for m in messages:
        original_role = m.get("role", "user")
        role = _normalise_role(original_role)
        meta: Dict[str, Any] = {}
        if original_role != role:
            meta["original_role"] = original_role
        content = m.get("content", "")
        if not isinstance(content, str):
            import json as _json
            content = _json.dumps(content, default=str)
        item = ContextItem(
            role=role,
            content=content,
            tool_call_id=m.get("tool_call_id"),
            tool_calls=m.get("tool_calls"),
            name=m.get("name"),
            pinned=(role == "system"),
            metadata=meta,
        )
        items.append(item)
        if role == "user":
            last_user_idx = len(items) - 1
    if last_user_idx >= 0:
        items[last_user_idx].pinned = True
    return items


def from_items(items: List[ContextItem]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for it in items:
        msg: Dict[str, Any] = {
            "role": it.metadata.get("original_role", it.role),
            "content": it.content,
        }
        if it.tool_call_id:
            msg["tool_call_id"] = it.tool_call_id
        if it.tool_calls:
            msg["tool_calls"] = it.tool_calls
        if it.name:
            msg["name"] = it.name
        out.append(msg)
    return out
