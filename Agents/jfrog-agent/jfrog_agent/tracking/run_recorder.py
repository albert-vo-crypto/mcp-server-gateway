"""Map a completed JFrog agent run to BigQuery tracking records."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ..audit_log import AuditTrail
from ..settings import Settings
from .backend import TrackingBackend

logger = logging.getLogger(__name__)


def _truncate(text: str | None, max_len: int = 50000) -> str | None:
    if not text or not str(text).strip():
        return None
    s = str(text).strip()
    return s[:max_len] + "...[truncated]" if len(s) > max_len else s


def _tool_rows_from_findings(
    findings: list[dict[str, Any]], request_id: str, session_id: str | None, ts_str: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build trajectory + tool_invocation rows from JFrog graph findings."""
    trajectory: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []
    for idx, f in enumerate(findings):
        tool = f.get("tool") or "unknown"
        result = f.get("result")
        preview = _truncate(str(result), 2000)
        is_err = bool(f.get("is_error"))
        tools.append(
            {
                "ts": ts_str,
                "request_id": request_id,
                "session_id": session_id,
                "step_index": idx,
                "tool_name": tool,
                "input_summary": f.get("intent") or None,
                "output_summary": preview,
                "success": not is_err,
                "duration_ms": None,
                "error_message": preview if is_err else None,
            }
        )
        trajectory.append(
            {
                "ts": ts_str,
                "request_id": request_id,
                "session_id": session_id,
                "step_index": idx,
                "step_type": "tool_result",
                "tool_name": tool,
                "input_summary": f.get("intent") or None,
                "output_summary": preview,
                "state_snapshot": None,
                "duration_ms": None,
                "error_message": preview if is_err else None,
                "metadata": [],
            }
        )
    return trajectory, tools


def record_jfrog_run(
    tracking: TrackingBackend,
    *,
    run_id: str,
    thread_id: str | None,
    request: str,
    result: dict[str, Any],
    audit: AuditTrail,
    settings: Settings,
    start_ts: datetime | None = None,
    end_ts: datetime | None = None,
) -> None:
    """Flush a completed graph run to the active tracking backend."""
    now = datetime.now(timezone.utc)
    start_ts = start_ts or now
    end_ts = end_ts or now
    outcome = (result.get("outcome") or "").lower()
    status = "complete" if outcome == "completed" else "error"
    findings = list(result.get("findings") or [])
    tool_call_count = sum(1 for e in audit.events if e.get("kind") == "tool_call")
    step_count = len(audit.events) or None

    kwargs: dict[str, Any] = {
        "request_id": run_id,
        "session_id": thread_id,
        "input_text": request,
        "output_text": result.get("answer") or "",
        "start_ts": start_ts,
        "end_ts": end_ts,
        "status": status,
        "agent_name": settings.client_name,
        "tool_call_count": tool_call_count or len(findings) or None,
        "step_count": step_count,
        "model_id": settings.llm_model,
        "metadata": _ctx_opt_metadata(),
    }

    # BigQuery client accepts custom trajectory via pre-built rows when we extend record_run
    if hasattr(tracking, "_client"):
        ts_str = now.strftime("%Y-%m-%d %H:%M:%S.%f")
        traj, tool_rows = _tool_rows_from_findings(findings, run_id, thread_id, ts_str)
        kwargs["_trajectory_rows"] = traj
        kwargs["_tool_invocation_rows"] = tool_rows

    try:
        tracking.record_run(**kwargs)
    except Exception as exc:
        logger.warning("record_jfrog_run failed: %s", exc)


def _ctx_opt_metadata() -> list[dict[str, str]]:
    try:
        from ..context_optimizer import is_enabled
        from ..context_optimizer.stats import metadata_for_bq

        meta = metadata_for_bq()
        if not any(m.get("key") == "ctx_opt_enabled" for m in meta):
            meta = [{"key": "ctx_opt_enabled", "value": "true" if is_enabled() else "false"}, *meta]
        return meta
    except Exception:
        return [{"key": "ctx_opt_enabled", "value": "false"}]
