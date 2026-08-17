"""
Compression layer — squeezes the content of individual items without
removing them. Three implementations:

    ToolResultCompression  trim oversized tool outputs (e.g. BigQuery tables)
                           via head/tail row-keeping or char-cap-with-marker
    MessageDedup           drop exact-text duplicates (defensive: some
                           orchestrators re-append the system prompt every
                           turn, leading to N copies of the same text)
    PromptSummarizer       fold the older non-pinned window into one
                           dense summary item via a caller-supplied LLM fn
"""

from __future__ import annotations

import logging
from typing import Callable, List, Optional

from ..core.item import ContextItem
from ..core.pipeline import Layer, OptimizationContext
from ..core.tokens import count_items_tokens

log = logging.getLogger(__name__)


class ToolResultCompression(Layer):
    """
    Trim oversized ``role="tool"`` items in place.

    Strategy:
      1. If content is line-delimited (looks like a table or JSONL) and has
         more than (head_rows + tail_rows + 4) lines, keep the first
         ``head_rows`` and last ``tail_rows`` and elide the middle.
      2. Otherwise cap at ``max_chars`` characters and append a marker.

    Never touches ``role="assistant"`` items — that would corrupt the
    assistant → tool_call linkage.
    """

    name = "compression:tool_results"

    def __init__(
        self,
        *,
        max_chars: int = 4000,
        head_rows: int = 20,
        tail_rows: int = 5,
    ):
        self.max_chars = max_chars
        self.head_rows = head_rows
        self.tail_rows = tail_rows

    def apply(
        self, items: List[ContextItem], ctx: OptimizationContext
    ) -> List[ContextItem]:
        out: List[ContextItem] = []
        for it in items:
            if not it.is_tool_response:
                out.append(it)
                continue
            text = it.content or ""
            if len(text) <= self.max_chars:
                out.append(it)
                continue
            lines = text.split("\n")
            if len(lines) > self.head_rows + self.tail_rows + 4:
                head = lines[: self.head_rows]
                tail = lines[-self.tail_rows :]
                omitted = len(lines) - len(head) - len(tail)
                new_text = (
                    "\n".join(head)
                    + f"\n... <{omitted} rows omitted by ToolResultCompression> ...\n"
                    + "\n".join(tail)
                )
            else:
                new_text = (
                    text[: self.max_chars]
                    + f"\n... <{len(text) - self.max_chars} chars truncated by ToolResultCompression> ..."
                )
            out.append(it.with_content(new_text).with_metadata(
                tool_result_compressed=True
            ))
        return out


class OversizedContentCompression(Layer):
    """
    Trim any item (any role) whose content exceeds ``max_chars``.

    Unlike ``ToolResultCompression``, this also targets large ``user`` items
    such as JFrog summarize payloads (``Findings (JSON): ...``) that would
    otherwise stay pinned and bypass the token budget.
    """

    name = "compression:oversized_content"

    def __init__(
        self,
        *,
        max_chars: int = 4000,
        head_rows: int = 20,
        tail_rows: int = 5,
    ):
        self.max_chars = max_chars
        self.head_rows = head_rows
        self.tail_rows = tail_rows

    def apply(
        self, items: List[ContextItem], ctx: OptimizationContext
    ) -> List[ContextItem]:
        out: List[ContextItem] = []
        for it in items:
            text = it.content or ""
            if len(text) <= self.max_chars:
                out.append(it)
                continue
            lines = text.split("\n")
            if len(lines) > self.head_rows + self.tail_rows + 4:
                head = lines[: self.head_rows]
                tail = lines[-self.tail_rows :]
                omitted = len(lines) - len(head) - len(tail)
                new_text = (
                    "\n".join(head)
                    + f"\n... <{omitted} lines omitted by OversizedContentCompression> ...\n"
                    + "\n".join(tail)
                )
            else:
                new_text = (
                    text[: self.max_chars]
                    + f"\n... <{len(text) - self.max_chars} chars truncated by OversizedContentCompression> ..."
                )
            out.append(it.with_content(new_text).with_metadata(content_compressed=True))
        return out


class MessageDedup(Layer):
    """
    Drop exact-content duplicates (case-sensitive). Keeps the first
    occurrence.

    Important: items whose identity is NOT their text — assistant messages
    that carry ``tool_calls``, and tool-response messages — are never
    deduped here. Their identity is the call_id, not the content. Use
    ``RepeatedToolCallMerger`` (clustering layer) to handle real repeats
    of those.

    Pinned items are always kept on first occurrence; pinned items that
    duplicate an earlier pinned item are also dropped, but a pinned item
    duplicating a non-pinned item upgrades that earlier item to pinned
    and the later pinned copy is dropped.
    """

    name = "compression:dedup_messages"

    def apply(
        self, items: List[ContextItem], ctx: OptimizationContext
    ) -> List[ContextItem]:
        seen: dict[tuple, int] = {}    # (role, content) -> index in `out`
        out: List[ContextItem] = []
        for it in items:
            # NEVER dedup identity-bearing items. Their semantic identity
            # is the tool_call_id, not the text — and dropping one without
            # also dropping its partner breaks the LLM contract.
            if it.has_tool_calls or it.is_tool_response:
                out.append(it)
                continue
            key = (it.role, it.content)
            if key in seen:
                if it.pinned and not out[seen[key]].pinned:
                    out[seen[key]].pinned = True
                continue
            seen[key] = len(out)
            out.append(it)
        return out


# A summarizer-style content compressor. Kept here because it compresses
# CONTENT (it folds N items into one summary item) — it does not pick which
# items to keep based on budget (that's Selection's job).
class PromptSummarizer(Layer):
    """
    Replace the older non-pinned window with a single SystemMessage summary
    produced by ``llm_call(items, instruction) -> str``.

    Trigger: only runs when current tokens > ``trigger_threshold * max_tokens``.
    The most recent ``keep_recent`` items are preserved verbatim. Pinned
    items are preserved.

    Useful as the last compression step before Selection if you want to
    keep a longer-tail memory than a sliding window allows.
    """

    name = "compression:prompt_summarizer"

    DEFAULT_INSTRUCTION = (
        "Compress the following chat history into one dense paragraph that "
        "preserves: (1) every datum the assistant has produced or learned, "
        "(2) every decision and tool result, (3) every user constraint. "
        "Drop greetings and rephrasing. Output only the summary."
    )

    def __init__(
        self,
        llm_call: Callable[[List[ContextItem], str], str],
        *,
        keep_recent: int = 6,
        trigger_threshold: float = 0.85,
        instruction: Optional[str] = None,
    ):
        self.llm_call = llm_call
        self.keep_recent = keep_recent
        self.trigger_threshold = trigger_threshold
        self.instruction = instruction or self.DEFAULT_INSTRUCTION

    def apply(
        self, items: List[ContextItem], ctx: OptimizationContext
    ) -> List[ContextItem]:
        current = count_items_tokens(items, ctx.model)
        if current <= ctx.max_tokens * self.trigger_threshold:
            return items
        non_pinned = [it for it in items if not it.pinned]
        if len(non_pinned) <= self.keep_recent:
            return items

        to_fold = non_pinned[: -self.keep_recent]
        keep_recent = non_pinned[-self.keep_recent :]
        pinned_items = [it for it in items if it.pinned]
        if not to_fold:
            return items

        try:
            summary = self.llm_call(to_fold, self.instruction)
        except Exception as exc:
            log.warning("PromptSummarizer LLM call failed: %s", exc)
            return items
        if not isinstance(summary, str) or not summary.strip():
            return items

        summary_item = ContextItem(
            role="system",
            content=(
                f"[Conversation summary of {len(to_fold)} earlier messages]\n"
                + summary.strip()
            ),
            pinned=True,
            metadata={"summarized_count": len(to_fold)},
        )
        return pinned_items + [summary_item] + keep_recent
