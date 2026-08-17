"""Apply context optimizer before JFrog agent LLM calls."""

from __future__ import annotations

from typing import Any, List, Optional

from . import get_optimizer
from .stats import record_optimize_meta


def apply_context_optimizer(
    messages: List[Any],
    *,
    task: Optional[str] = None,
    purpose: str = "llm",
) -> List[Any]:
    """Run the layered optimizer on LangChain messages; no-op when disabled."""
    optimizer = get_optimizer()
    if optimizer is None:
        return messages
    try:
        out, meta = optimizer.optimize(messages, task=task)
        saved_str = f" ${meta.saved_usd:.6f}" if meta.saved_usd is not None else ""
        tag = "saved" if meta.saved > 0 else ("no-op" if meta.saved == 0 else "grew")
        print(
            f"  ctx-opt[{meta.strategy_used}] {tag} ({purpose}): "
            f"{meta.input_tokens} -> {meta.output_tokens} tokens "
            f"(delta {meta.saved:+d}{saved_str}, ratio {meta.compression_ratio:.2f}, "
            f"tool_msgs_compressed={meta.tool_messages_compressed})"
        )
        record_optimize_meta(purpose, meta)
        return out
    except Exception as exc:  # noqa: BLE001 - never break the agent
        print(f"  ctx-opt: skipped ({purpose}) due to error: {exc}")
        from .stats import record_optimize_error

        record_optimize_error(purpose, str(exc))
        return messages
