"""Per-run context optimizer statistics for BQ tracking metadata."""

from __future__ import annotations

from typing import Any

from . import OptimizeMeta

_stats: dict[str, Any] = {
    "calls": 0,
    "tokens_before": 0,
    "tokens_after": 0,
    "saved": 0,
    "elapsed_ms": 0.0,
    "purposes": [],
    "preset": None,
    "errors": [],
}


def reset_run_stats() -> None:
    _stats.update(
        {
            "calls": 0,
            "tokens_before": 0,
            "tokens_after": 0,
            "saved": 0,
            "elapsed_ms": 0.0,
            "purposes": [],
            "preset": None,
            "errors": [],
        }
    )


def record_optimize_error(purpose: str, error: str) -> None:
    _stats["errors"].append({"purpose": purpose, "error": error[:200]})


def record_optimize_meta(purpose: str, meta: OptimizeMeta) -> None:
    _stats["calls"] += 1
    _stats["tokens_before"] += int(meta.input_tokens or 0)
    _stats["tokens_after"] += int(meta.output_tokens or 0)
    _stats["saved"] += int(meta.saved or 0)
    _stats["elapsed_ms"] += float(meta.elapsed_ms or 0.0)
    _stats["purposes"].append(purpose)
    _stats["preset"] = meta.strategy_used


def get_run_stats() -> dict[str, Any]:
    return dict(_stats)


def metadata_for_bq() -> list[dict[str, str]]:
    from . import is_enabled

    if not _stats["calls"] and not _stats.get("errors"):
        return []
    rows = [
        {"key": "ctx_opt_enabled", "value": "true" if is_enabled() else "false"},
    ]
    if _stats["calls"]:
        rows.extend([
            {"key": "ctx_opt_calls", "value": str(_stats["calls"])},
            {"key": "ctx_opt_tokens_before", "value": str(_stats["tokens_before"])},
            {"key": "ctx_opt_tokens_after", "value": str(_stats["tokens_after"])},
            {"key": "ctx_opt_tokens_saved", "value": str(_stats["saved"])},
            {"key": "ctx_opt_elapsed_ms", "value": str(int(_stats["elapsed_ms"]))},
        ])
        if _stats.get("preset"):
            rows.append({"key": "ctx_opt_preset", "value": str(_stats["preset"])})
        if _stats["tokens_before"]:
            pct = int(100 * _stats["saved"] / _stats["tokens_before"])
            rows.append({"key": "ctx_opt_saved_pct", "value": str(pct)})
    if _stats.get("errors"):
        rows.append({"key": "ctx_opt_error", "value": str(_stats["errors"][0].get("error", "unknown"))[:500]})
    return rows
