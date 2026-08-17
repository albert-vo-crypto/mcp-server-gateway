"""BigQuery tracking backend — production eval/tracking store."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from google.cloud import bigquery

from ..settings import Settings, settings as default_settings
from .backend import TrackingBackend
from .bq_client import TrackingClient
from .config import BQ_TRACKING_DATASET_ID, BQ_TRACKING_PROJECT_ID, EVAL_SCORES_TABLE_NAME

logger = logging.getLogger(__name__)


def _meta_value(metadata: list[dict] | None, key: str) -> int | None:
    if not metadata:
        return None
    for item in metadata:
        if item.get("key") == key:
            try:
                return int(item["value"])
            except (TypeError, ValueError, KeyError):
                return None
    return None


class BigQueryTrackingBackend(TrackingBackend):
    """Writes agent runs to BigQuery; buffers per-call LLM metrics until record_run."""

    def __init__(self, settings: Settings = default_settings):
        self.s = settings
        self._client = TrackingClient(
            project_id=settings.bq_project_id,
            dataset_id=settings.bq_dataset_id,
        )
        self._run_buffer: dict[str, list[dict[str, Any]]] = {}

    def _buffer_key(self, run_id: str | None) -> str:
        return run_id or "_unknown"

    def record_llm_call(self, **kw: Any) -> None:
        run_id = kw.get("run_id")
        self._run_buffer.setdefault(self._buffer_key(run_id), []).append(dict(kw))

    def _aggregate_buffered(self, run_id: str | None) -> dict[str, Any]:
        rows = self._run_buffer.pop(self._buffer_key(run_id), [])
        input_tokens = sum(int(r.get("prompt_tokens") or 0) for r in rows)
        output_tokens = sum(int(r.get("completion_tokens") or 0) for r in rows)
        total_tokens = sum(int(r.get("total_tokens") or 0) for r in rows)
        model = next((r.get("model") for r in rows if r.get("model")), None)
        return {
            "llm_call_count": len(rows) or None,
            "input_tokens": input_tokens or None,
            "output_tokens": output_tokens or None,
            "total_tokens": total_tokens or None,
            "model_id": model,
        }

    def record_run(self, **kwargs: Any) -> None:
        run_id = kwargs.get("request_id") or kwargs.get("run_id")
        agg = self._aggregate_buffered(run_id)
        merged = {**agg, **{k: v for k, v in kwargs.items() if v is not None}}
        try:
            self._client.record_agent_run(**merged)
        except Exception as exc:
            logger.warning("BigQuery tracking write failed (run_id=%s): %s", run_id, exc)

    def record_feedback(self, run_id: str | None, thread_id: str | None, rating: int, note: str = "") -> None:
        if not run_id:
            return
        now = datetime.now(timezone.utc)
        table_id = f"{self.s.bq_project_id}.{self.s.bq_dataset_id}.{EVAL_SCORES_TABLE_NAME}"
        row = {
            "traffic_date": now.strftime("%Y-%m-%d"),
            "eval_run_id": run_id,
            "request_id": run_id,
            "agent_name": self.s.client_name,
            "task_success_score": 1.0 if rating > 0 else 0.0,
            "overall_score": 1.0 if rating > 0 else 0.0,
            "dimensions_metadata": json.dumps({"feedback_rating": rating, "note": note, "thread_id": thread_id}),
            "ts": now.strftime("%Y-%m-%d %H:%M:%S.%f"),
        }
        try:
            self._client.create_table_if_not_exists()
            self._client._ensure_eval_scores_table()
            self._client._append_rows(table_id, [row])
        except Exception as exc:
            logger.warning("BigQuery feedback write failed: %s", exc)

    def _query(self, sql: str, params: list[bigquery.ScalarQueryParameter] | None = None) -> list[dict]:
        job_config = bigquery.QueryJobConfig(query_parameters=params or [])
        rows = self._client.client.query(sql, job_config=job_config).result()
        return [dict(r.items()) for r in rows]

    def list_llm_calls(self, limit: int = 500) -> list[dict[str, Any]]:
        table = f"`{BQ_TRACKING_PROJECT_ID}.{BQ_TRACKING_DATASET_ID}.agent_job_tracking`"
        sql = f"""
            SELECT ts, request_id AS run_id, session_id AS thread_id, model_id AS model,
                   duration_ms AS latency_ms, metadata, status
            FROM {table}
            ORDER BY ts DESC
            LIMIT @lim
        """
        params = [bigquery.ScalarQueryParameter("lim", "INT64", int(limit))]
        out = []
        for r in self._query(sql, params):
            meta = r.get("metadata") or []
            out.append(
                {
                    "run_id": r.get("run_id"),
                    "thread_id": r.get("thread_id"),
                    "purpose": "run",
                    "provider": "agent",
                    "model": r.get("model"),
                    "prompt_tokens": _meta_value(meta, "input_tokens"),
                    "completion_tokens": _meta_value(meta, "output_tokens"),
                    "total_tokens": _meta_value(meta, "total_tokens"),
                    "latency_ms": r.get("latency_ms"),
                    "ok": (r.get("status") or "").lower() == "complete",
                    "cost": None,
                    "ts": r.get("ts").timestamp() if hasattr(r.get("ts"), "timestamp") else r.get("ts"),
                }
            )
        return out

    def llm_summary(self) -> dict[str, Any]:
        table = f"`{BQ_TRACKING_PROJECT_ID}.{BQ_TRACKING_DATASET_ID}.agent_job_tracking`"
        rows = self._query(
            f"SELECT model_id, duration_ms, metadata FROM {table} ORDER BY ts DESC LIMIT 5000"
        )
        calls = len(rows)
        tokens = 0
        cost = 0.0
        latencies: list[float] = []
        by_model: dict[str, dict[str, Any]] = {}
        for r in rows:
            meta = r.get("metadata") or []
            tt = _meta_value(meta, "total_tokens") or 0
            tokens += tt
            model = r.get("model_id") or "unknown"
            bucket = by_model.setdefault(
                model, {"model": model, "calls": 0, "tokens": 0, "cost": 0.0, "avg_latency_ms": 0.0}
            )
            bucket["calls"] += 1
            bucket["tokens"] += tt
            if r.get("duration_ms") is not None:
                latencies.append(float(r["duration_ms"]))
        avg_lat = sum(latencies) / len(latencies) if latencies else 0.0
        for b in by_model.values():
            b["avg_latency_ms"] = avg_lat
        return {
            "calls": calls,
            "tokens": tokens,
            "cost": cost,
            "avg_latency_ms": avg_lat,
            "by_model": list(by_model.values()),
        }

    def feedback_summary(self) -> dict[str, Any]:
        table = f"`{BQ_TRACKING_PROJECT_ID}.{BQ_TRACKING_DATASET_ID}.{EVAL_SCORES_TABLE_NAME}`"
        try:
            rows = self._query(
                f"SELECT dimensions_metadata FROM {table} WHERE dimensions_metadata IS NOT NULL LIMIT 5000"
            )
        except Exception:
            return {"total": 0, "up": 0, "down": 0}
        up = down = 0
        for r in rows:
            try:
                meta = json.loads(r.get("dimensions_metadata") or "{}")
            except json.JSONDecodeError:
                continue
            rating = meta.get("feedback_rating")
            if rating is None:
                continue
            if int(rating) > 0:
                up += 1
            elif int(rating) < 0:
                down += 1
        return {"total": up + down, "up": up, "down": down}
