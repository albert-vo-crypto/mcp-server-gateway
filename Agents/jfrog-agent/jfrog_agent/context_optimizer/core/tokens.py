"""
Framework-agnostic token counting for ContextItem lists.

Uses tiktoken with model-specific encoding when known, falling back to
cl100k_base. Adds a fixed per-message overhead (4 tokens) to approximate
OpenAI's chat-completion role/formatting wrapping. Reasonable proxy for
Anthropic models too (Claude tokenizer is closer to GPT than ALBERT).
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Iterable, List

import tiktoken

from .item import ContextItem

PER_MESSAGE_OVERHEAD_TOKENS = 4
_CHARS_PER_TOKEN_EST = 4  # fallback when tiktoken encodings unavailable offline


@lru_cache(maxsize=16)
def _get_encoding(model: str) -> tiktoken.Encoding | None:
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        pass
    except Exception:
        pass
    try:
        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


def _estimate_tokens(text: str) -> int:
    """Rough token estimate when tiktoken cannot load encodings (offline/SSL)."""
    return max(1, len(text or "") // _CHARS_PER_TOKEN_EST)


def _encode_len(text: str, enc: tiktoken.Encoding | None) -> int:
    if enc is None:
        return _estimate_tokens(text)
    return len(enc.encode(text or ""))


def count_item_tokens(item: ContextItem, model: str = "gpt-4o-mini") -> int:
    """Token count for a single ContextItem, including per-message overhead."""
    enc = _get_encoding(model)
    n = _encode_len(item.content or "", enc)
    if item.has_tool_calls:
        for tc in item.tool_calls or []:
            n += _encode_len(str(tc.get("name", "")), enc)
            args = tc.get("args") or tc.get("arguments") or {}
            if isinstance(args, str):
                n += _encode_len(args, enc)
            else:
                n += _encode_len(json.dumps(args, default=str), enc)
    if item.tool_call_id:
        n += _encode_len(item.tool_call_id, enc)
    return n + PER_MESSAGE_OVERHEAD_TOKENS


def count_items_tokens(
    items: Iterable[ContextItem], model: str = "gpt-4o-mini"
) -> int:
    """Token count for an iterable of ContextItems. 0 if empty."""
    total = 0
    for it in items:
        total += count_item_tokens(it, model)
    return total


def cache_tokens_on_items(
    items: List[ContextItem], model: str = "gpt-4o-mini"
) -> List[ContextItem]:
    """
    Tag each item's metadata with `token_count` so later layers can read it
    without recomputing. Returns the same list (mutated in place is OK here
    because metadata is layer scratch space).
    """
    for it in items:
        it.metadata["token_count"] = count_item_tokens(it, model)
    return items
