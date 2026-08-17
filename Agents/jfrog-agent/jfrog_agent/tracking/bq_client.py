"""
BigQuery client for writing Agent EVAL tracking records.

Writes each agent run (query/task) to ck-deveff-ai-mcp-hub.genai_agent_tracking.<repo>_JobTracking
so the Evaluator framework can ingest into GenAI_Evaluation_Response_Detail and related tables.
Table is created if it does not exist.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from google.api_core import exceptions as google_exceptions
from google.cloud import bigquery

from .config import (
    BQ_TRACKING_DATASET_ID,
    BQ_TRACKING_PROJECT_ID,
    BQ_TRACKING_TABLE_NAME,
    QUALITY_EVAL_TABLE_NAME,
    EVAL_SCORES_TABLE_NAME,
    QUALITY_METRICS_DETAIL_TABLE_NAME,
    TRAJECTORY_TABLE_NAME,
    TOOL_INVOCATIONS_TABLE_NAME,
    SERVICE_NAME,
)
from .cost_estimate import estimate_llm_cost_usd
from .table_schema import (
    PARTITION_COLUMN,
    PARTITION_EXPIRATION_DAYS,
    SCHEMA_AGENT_JOB_TRACKING,
    SCHEMA_QUALITY_EVAL,
    QUALITY_EVAL_PARTITION_COLUMN,
    QUALITY_EVAL_METRIC_NAMES,
    SCHEMA_AGENT_EVAL_SCORES,
    EVAL_SCORES_PARTITION_COLUMN,
    SCHEMA_AGENT_QUALITY_METRICS_DETAIL,
    QUALITY_METRICS_DETAIL_PARTITION_COLUMN,
    SCHEMA_AGENT_TRAJECTORY,
    TRAJECTORY_PARTITION_COLUMN,
    SCHEMA_AGENT_TOOL_INVOCATIONS,
    TOOL_INVOCATIONS_PARTITION_COLUMN,
    patch_skill_columns_if_missing,
    patch_quality_metrics_detail_nested_columns_if_missing,
)

logger = logging.getLogger(__name__)


def _make_bigquery_client(project_id: str) -> bigquery.Client:
    """Create a BigQuery client: JSON key, WIF external_account JSON, or ADC."""
    import os

    import google.auth

    key_path = os.environ.get("BIGQUERY_CREDENTIALS_PATH") or os.environ.get(
        "GOOGLE_APPLICATION_CREDENTIALS"
    )
    if key_path and os.path.isfile(key_path):
        try:
            credentials, _ = google.auth.load_credentials_from_file(key_path)
            return bigquery.Client(project=project_id, credentials=credentials)
        except (ValueError, OSError) as e:
            logger.warning(
                "Could not load credentials from %s (%s); falling back to ADC",
                key_path,
                e,
            )
    credentials, _ = google.auth.default()
    return bigquery.Client(project=project_id, credentials=credentials)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _bq_timestamp(dt: datetime) -> str:
    """Format datetime for BigQuery streaming insert (TIMESTAMP: YYYY-MM-DD HH:MM:SS.ffffff)."""
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")


def _sanitize_for_storage(text: str | None, max_len: int = 50000) -> str | None:
    """Optionally truncate and sanitize text for BQ storage. Returns None for empty."""
    if text is None or not text.strip():
        return None
    s = text.strip()
    if len(s) > max_len:
        return s[:max_len] + "...[truncated]"
    return s


def _truncate_summary(text: str | None, max_len: int = 2000) -> str | None:
    """Truncate for trajectory/tool summary fields."""
    if text is None or not str(text).strip():
        return None
    s = str(text).strip()
    return s[:max_len] + "..." if len(s) > max_len else s


class TrackingClient:
    """Writes agent run records to the GenAI Guidance Job Tracking BigQuery table."""

    def __init__(
        self,
        project_id: str | None = None,
        dataset_id: str | None = None,
        table_name: str | None = None,
    ):
        self.project_id = project_id or BQ_TRACKING_PROJECT_ID
        self.dataset_id = dataset_id or BQ_TRACKING_DATASET_ID
        self.table_name = table_name or BQ_TRACKING_TABLE_NAME
        self._client: bigquery.Client | None = None

    @property
    def client(self) -> bigquery.Client:
        if self._client is None:
            self._client = _make_bigquery_client(self.project_id)
        return self._client

    def table_id(self) -> str:
        return f"{self.project_id}.{self.dataset_id}.{self.table_name}"

    def _create_dataset_if_not_exists(self) -> None:
        """Create the dataset if it does not exist."""
        dataset_id = f"{self.project_id}.{self.dataset_id}"
        try:
            self.client.get_dataset(dataset_id)
            return
        except Exception:
            pass
        try:
            dataset = bigquery.Dataset(dataset_id)
            self.client.create_dataset(dataset)
            logger.info("Created dataset: %s", dataset_id)
        except google_exceptions.Conflict:
            pass

    def create_table_if_not_exists(self) -> bigquery.Table:
        """Create the tracking table if it does not exist (partitioned by ts, 90-day expiration)."""
        table_id = self.table_id()
        try:
            table = self.client.get_table(table_id)
            patch_skill_columns_if_missing(self.client, table_id)
            return table
        except Exception:
            pass
        self._create_dataset_if_not_exists()
        table = bigquery.Table(table_id, schema=SCHEMA_AGENT_JOB_TRACKING)
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field=PARTITION_COLUMN,
            expiration_ms=PARTITION_EXPIRATION_DAYS * 24 * 60 * 60 * 1000,
        )
        table = self.client.create_table(table)
        logger.info("Created Agent EVAL tracking table: %s", table_id)
        return table

    def _append_rows(self, table_id: str, rows: list[dict[str, Any]]) -> None:
        """Append rows via streaming insert, falling back to load job on free tier."""
        if not rows:
            return
        try:
            errors = self.client.insert_rows_json(table_id, rows)
            if not errors:
                return
            logger.warning("BigQuery streaming insert errors (trying load job): %s", errors)
        except Exception as exc:
            msg = str(exc)
            if "Streaming insert is not allowed" not in msg and "403" not in msg:
                raise
            logger.info("Streaming insert unavailable; using load job append")
        job = self.client.load_table_from_json(
            rows,
            table_id,
            job_config=bigquery.LoadJobConfig(
                write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            ),
        )
        job.result()

    def insert_row(self, row: dict[str, Any]) -> None:
        """Insert a single row into the tracking table. Creates table if needed."""
        self.create_table_if_not_exists()
        self._append_rows(self.table_id(), [row])

    def record_agent_run(
        self,
        *,
        request_id: str,
        session_id: str,
        input_text: str,
        output_text: str,
        start_ts: datetime | None = None,
        end_ts: datetime | None = None,
        experience_id: str | None = None,
        agent_name: str | None = None,
        status: str | None = None,
        error_message: str | None = None,
        trace_id: str | None = None,
        model_id: str | None = None,
        metadata: list[dict[str, str]] | None = None,
        **extra: Any,
    ) -> None:
        """
        Record one agent run to the generic BigQuery table (any agent use case).
        Timestamps in UTC; duration_ms computed from start/end.
        """
        messages_for_trajectory = extra.pop("_messages", None)
        trajectory_rows_override = extra.pop("_trajectory_rows", None)
        tool_invocation_rows_override = extra.pop("_tool_invocation_rows", None)

        now = _utc_now()
        start_ts = start_ts or now
        end_ts = end_ts or now
        duration_ms = None
        try:
            duration_ms = int((end_ts - start_ts).total_seconds() * 1000)
        except (TypeError, ValueError):
            pass

        # Populate model_id (GenOS/LLM name) and token usage in row + metadata for visibility in BQ
        resolved_model_id = model_id or extra.get("model_id")
        input_tokens = extra.get("input_tokens")
        output_tokens = extra.get("output_tokens")
        total_tokens = extra.get("total_tokens")
        meta = list(metadata or [])
        if resolved_model_id:
            meta.append({"key": "llm_model", "value": str(resolved_model_id)})
        if input_tokens is not None:
            meta.append({"key": "input_tokens", "value": str(input_tokens)})
        if output_tokens is not None:
            meta.append({"key": "output_tokens", "value": str(output_tokens)})
        if total_tokens is not None:
            meta.append({"key": "total_tokens", "value": str(total_tokens)})

        skill_directory = extra.pop("skill_directory", None)
        skill_name = extra.pop("skill_name", None)
        skill_channel = extra.pop("skill_channel", None)
        if skill_directory:
            meta.append({"key": "skill_directory", "value": str(skill_directory)})
        if skill_name:
            meta.append({"key": "skill_name", "value": str(skill_name)})
        if skill_channel:
            meta.append({"key": "skill_channel", "value": str(skill_channel)})

        if extra.get("tool_call_count") is not None:
            meta.append(
                {"key": "tool_call_count", "value": str(extra["tool_call_count"])}
            )
        if extra.get("llm_call_count") is not None:
            meta.append(
                {"key": "llm_call_count", "value": str(extra["llm_call_count"])}
            )

        row = {
            "ts": _bq_timestamp(now),
            "request_id": request_id,
            "session_id": session_id or None,
            "trace_id": trace_id or None,
            "agent_name": agent_name or SERVICE_NAME,
            "experience_id": experience_id or None,
            "input": input_text or None,
            "input_sanitized": _sanitize_for_storage(input_text),
            "output": output_text or None,
            "output_sanitized": _sanitize_for_storage(output_text),
            "status": status or "complete",
            "error_message": error_message or None,
            "start_ts": _bq_timestamp(start_ts),
            "end_ts": _bq_timestamp(end_ts),
            "duration_ms": duration_ms,
            "model_id": resolved_model_id,
            "skill_directory": skill_directory or None,
            "skill_name": skill_name or None,
            "skill_channel": skill_channel or None,
            "metadata": meta,
        }
        self.insert_row(row)
        logger.info("Agent EVAL: wrote 1 row to %s", self.table_id())

        # Write all quality dimensions to agent_quality_eval (real values + placeholders for evaluator)
        self._write_quality_metrics(
            request_id=request_id,
            agent_name=agent_name or SERVICE_NAME,
            eval_run_id=request_id,
            status=status or "complete",
            duration_ms=duration_ms,
            step_count=extra.get("step_count"),
            tool_call_count=extra.get("tool_call_count"),
            llm_call_count=extra.get("llm_call_count"),
            total_tokens=extra.get("total_tokens"),
            input_tokens=extra.get("input_tokens"),
            output_tokens=extra.get("output_tokens"),
        )
        # Write multi-dimensional scores to agent_eval_scores (agent fills what it can; evaluator fills rest)
        self._write_eval_scores(
            request_id=request_id,
            agent_name=agent_name or SERVICE_NAME,
            eval_run_id=request_id,
            status=status or "complete",
            duration_ms=duration_ms,
            skill_directory=skill_directory or None,
            skill_name=skill_name or None,
            skill_channel=skill_channel or None,
        )
        # Write in-depth quality metrics row (agent fills latency_cost, agent_metrics; evaluator fills rest)
        self._write_quality_metrics_detail(
            request_id=request_id,
            session_id=session_id,
            agent_name=agent_name or SERVICE_NAME,
            model_id=resolved_model_id,
            status=status or "complete",
            duration_ms=duration_ms,
            step_count=extra.get("step_count"),
            tool_call_count=extra.get("tool_call_count"),
            input_tokens=extra.get("input_tokens"),
            output_tokens=extra.get("output_tokens"),
            total_tokens=extra.get("total_tokens"),
            skill_directory=skill_directory or None,
            skill_name=skill_name or None,
            skill_channel=skill_channel or None,
        )
        # Trajectory / tool tables: parse LangGraph messages when present; still ensure
        # empty tables exist on error-only runs so BQ shows the resources.
        self._write_trajectory_and_tool_invocations(
            messages_for_trajectory or [],
            request_id=request_id,
            session_id=session_id,
            trajectory_rows=trajectory_rows_override,
            tool_invocation_rows=tool_invocation_rows_override,
        )

    def _quality_eval_table_id(self) -> str:
        return f"{self.project_id}.{self.dataset_id}.{QUALITY_EVAL_TABLE_NAME}"

    def _ensure_quality_eval_table(self) -> None:
        """Create quality eval table if it does not exist."""
        table_id = self._quality_eval_table_id()
        try:
            self.client.get_table(table_id)
            return
        except Exception:
            pass
        self._create_dataset_if_not_exists()
        table = bigquery.Table(table_id, schema=SCHEMA_QUALITY_EVAL)
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field=QUALITY_EVAL_PARTITION_COLUMN,
        )
        self.client.create_table(table)
        logger.info("Created quality eval table: %s", table_id)

    def _write_quality_metrics(
        self,
        *,
        request_id: str,
        agent_name: str,
        eval_run_id: str,
        status: str,
        duration_ms: int | None,
        step_count: int | None = None,
        tool_call_count: int | None = None,
        llm_call_count: int | None = None,
        total_tokens: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        """Write all 8 quality metric rows to agent_quality_eval.

        Agent fills task_success, latency, step_efficiency, cost when available.
        """
        now = _utc_now()
        traffic_date = now.strftime("%Y-%m-%d")
        ts_str = _bq_timestamp(now)
        success = (status or "").lower() == "complete"

        step_eff_result = str(step_count) if step_count is not None else ""
        step_eff_expl = (
            f"{step_count} steps to complete task (avoids unnecessary actions when low)"
            if step_count is not None
            else "To be filled by evaluator (minimal steps)"
        )

        cost_parts = []
        if total_tokens is not None:
            cost_parts.append(f"total {total_tokens} tokens")
        if input_tokens is not None or output_tokens is not None:
            cost_parts.append(
                f"input: {input_tokens or 0}, output: {output_tokens or 0}"
            )
        if tool_call_count is not None:
            cost_parts.append(f"tool calls: {tool_call_count}")
        if llm_call_count is not None:
            cost_parts.append(f"LLM calls: {llm_call_count}")
        cost_result = str(total_tokens) if total_tokens is not None else ""
        cost_expl = (
            ". ".join(cost_parts)
            if cost_parts
            else "To be filled by evaluator (tokens/API cost)"
        )

        metric_result_and_explanation: dict[str, tuple[str, str]] = {
            "task_success_rate": (
                "1.0" if success else "0.0",
                "Goal achieved" if success else "Run did not complete successfully",
            ),
            "latency": (
                str(duration_ms) if duration_ms is not None else "",
                (
                    "End-to-end latency in milliseconds"
                    if duration_ms
                    else "Duration not available"
                ),
            ),
            "tool_selection_accuracy": (
                "",
                "To be filled by evaluator (whether agent picked the right tool for each step)",
            ),
            "parameter_correctness": (
                "",
                "To be filled by evaluator (valid tool arguments)",
            ),
            "step_efficiency": (step_eff_result, step_eff_expl),
            "safety_compliance": ("", "To be filled by evaluator (within policy)"),
            "cost": (cost_result, cost_expl),
            "recovery_resilience": (
                "",
                "To be filled by evaluator (handled failures and adapted)",
            ),
        }
        metrics = []
        for name in QUALITY_EVAL_METRIC_NAMES:
            result, explanation = metric_result_and_explanation.get(
                name, ("", "To be filled by evaluator")
            )
            metrics.append(
                {
                    "traffic_date": traffic_date,
                    "eval_run_id": eval_run_id,
                    "request_id": request_id,
                    "agent_name": agent_name,
                    "metric_name": name,
                    "result": result,
                    "explanation": explanation,
                    "metadata": None,
                    "ts": ts_str,
                }
            )
        self._ensure_quality_eval_table()
        self._append_rows(self._quality_eval_table_id(), metrics)
        logger.info(
            "Agent EVAL: wrote %d quality metric rows to %s",
            len(metrics),
            self._quality_eval_table_id(),
        )

    def _eval_scores_table_id(self) -> str:
        return f"{self.project_id}.{self.dataset_id}.{EVAL_SCORES_TABLE_NAME}"

    def _ensure_eval_scores_table(self) -> None:
        """Create agent_eval_scores table if it does not exist."""
        table_id = self._eval_scores_table_id()
        try:
            self.client.get_table(table_id)
            patch_skill_columns_if_missing(self.client, table_id)
            return
        except Exception:
            pass
        self._create_dataset_if_not_exists()
        table = bigquery.Table(table_id, schema=SCHEMA_AGENT_EVAL_SCORES)
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field=EVAL_SCORES_PARTITION_COLUMN,
        )
        self.client.create_table(table)
        logger.info("Created eval scores table: %s", table_id)

    def _write_eval_scores(
        self,
        *,
        request_id: str,
        agent_name: str,
        eval_run_id: str,
        status: str,
        duration_ms: int | None,
        skill_directory: str | None = None,
        skill_name: str | None = None,
        skill_channel: str | None = None,
    ) -> None:
        """Write one row to agent_eval_scores with all dimensions.

        Agent fills task_success + latency, rest for evaluator.
        """
        now = _utc_now()
        traffic_date = now.strftime("%Y-%m-%d")
        ts_str = _bq_timestamp(now)
        success = (status or "").lower() == "complete"
        task_success = 1.0 if success else 0.0
        # Overall = task_success when we only have that + latency; evaluator can overwrite with weighted blend
        overall = task_success
        row = {
            "traffic_date": traffic_date,
            "eval_run_id": eval_run_id,
            "request_id": request_id,
            "task_id": None,
            "trial_index": None,
            "agent_name": agent_name,
            "skill_directory": skill_directory,
            "skill_name": skill_name,
            "skill_channel": skill_channel,
            "task_success_score": task_success,
            "tool_accuracy_score": None,
            "parameter_correctness_score": None,
            "efficiency_score": None,
            "safety_score": None,
            "cost_score": None,
            "latency_ms": duration_ms,
            "recovery_resilience_score": None,
            "overall_score": overall,
            "dimensions_metadata": None,
            "ts": ts_str,
        }
        self._ensure_eval_scores_table()
        self._append_rows(self._eval_scores_table_id(), [row])
        logger.info("Agent EVAL: wrote 1 row to %s", self._eval_scores_table_id())

    def _quality_metrics_detail_table_id(self) -> str:
        return (
            f"{self.project_id}.{self.dataset_id}.{QUALITY_METRICS_DETAIL_TABLE_NAME}"
        )

    def _ensure_quality_metrics_detail_table(self) -> None:
        """Create agent_quality_metrics_detail table if it does not exist."""
        table_id = self._quality_metrics_detail_table_id()
        try:
            self.client.get_table(table_id)
            patch_skill_columns_if_missing(self.client, table_id)
            patch_quality_metrics_detail_nested_columns_if_missing(
                self.client, table_id
            )
            return
        except Exception:
            pass
        self._create_dataset_if_not_exists()
        table = bigquery.Table(table_id, schema=SCHEMA_AGENT_QUALITY_METRICS_DETAIL)
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field=QUALITY_METRICS_DETAIL_PARTITION_COLUMN,
        )
        self.client.create_table(table)
        logger.info("Created quality metrics detail table: %s", table_id)

    def _write_quality_metrics_detail(
        self,
        *,
        request_id: str,
        session_id: str | None,
        agent_name: str,
        model_id: str | None,
        status: str,
        duration_ms: int | None,
        step_count: int | None,
        tool_call_count: int | None,
        input_tokens: int | None,
        output_tokens: int | None,
        total_tokens: int | None = None,
        skill_directory: str | None = None,
        skill_name: str | None = None,
        skill_channel: str | None = None,
    ) -> None:
        """Write one row to agent_quality_metrics_detail; agent fills metadata, agent_metrics, latency_cost."""
        now = _utc_now()
        evaluated_date = now.strftime("%Y-%m-%d")
        ts_str = _bq_timestamp(now)
        success = (status or "").lower() == "complete"
        use_total = (input_tokens or 0) == 0 and (output_tokens or 0) == 0
        est_cost = estimate_llm_cost_usd(
            model_id,
            input_tokens,
            output_tokens,
            total_tokens=total_tokens if use_total else None,
        )
        row = {
            "evaluated_date": evaluated_date,
            "run_id": request_id,
            "session_id": session_id,
            "model_name": model_id,
            "prompt_version": None,
            "evaluated_at": ts_str,
            "evaluator_type": "agent",
            "agent_name": agent_name,
            "skill_directory": skill_directory,
            "skill_name": skill_name,
            "skill_channel": skill_channel,
            "response_quality": {
                "faithfulness_score": None,
                "answer_relevance_score": None,
                "context_precision_score": None,
                "context_recall_score": None,
            },
            "agent_metrics": {
                "task_completion_rate": 1.0 if success else 0.0,
                "tool_call_accuracy": None,
                "tool_call_count": tool_call_count,
                "step_count": step_count,
                "redundant_steps": None,
            },
            "latency_cost": {
                "latency_ms": duration_ms,
                "time_to_first_token_ms": None,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "estimated_cost_usd": est_cost,
            },
            "safety_alignment": {
                "hallucination_flag": None,
                "toxicity_score": None,
                "refusal_flag": None,
                "pii_detected": None,
            },
            "trace": [],
        }
        self._ensure_quality_metrics_detail_table()
        self._append_rows(self._quality_metrics_detail_table_id(), [row])
        logger.info(
            "Agent EVAL: wrote 1 row to %s", self._quality_metrics_detail_table_id()
        )

    def _trajectory_table_id(self) -> str:
        return f"{self.project_id}.{self.dataset_id}.{TRAJECTORY_TABLE_NAME}"

    def _tool_invocations_table_id(self) -> str:
        return f"{self.project_id}.{self.dataset_id}.{TOOL_INVOCATIONS_TABLE_NAME}"

    def _ensure_trajectory_table(self) -> None:
        table_id = self._trajectory_table_id()
        try:
            self.client.get_table(table_id)
            return
        except Exception:
            pass
        self._create_dataset_if_not_exists()
        table = bigquery.Table(table_id, schema=SCHEMA_AGENT_TRAJECTORY)
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field=TRAJECTORY_PARTITION_COLUMN,
            expiration_ms=PARTITION_EXPIRATION_DAYS * 24 * 60 * 60 * 1000,
        )
        self.client.create_table(table)
        logger.info("Created trajectory table: %s", table_id)

    def _ensure_tool_invocations_table(self) -> None:
        table_id = self._tool_invocations_table_id()
        try:
            self.client.get_table(table_id)
            return
        except Exception:
            pass
        self._create_dataset_if_not_exists()
        table = bigquery.Table(table_id, schema=SCHEMA_AGENT_TOOL_INVOCATIONS)
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field=TOOL_INVOCATIONS_PARTITION_COLUMN,
            expiration_ms=PARTITION_EXPIRATION_DAYS * 24 * 60 * 60 * 1000,
        )
        self.client.create_table(table)
        logger.info("Created tool invocations table: %s", table_id)

    def _parse_messages_to_trajectory_and_tools(
        self,
        messages: list,
        request_id: str,
        session_id: str | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Parse LangChain messages into trajectory rows and tool_invocation rows for BQ."""
        try:
            from langchain_core.messages import AIMessage, ToolMessage
        except ImportError:
            return [], []

        now = _utc_now()
        ts_str = _bq_timestamp(now)
        trajectory_rows: list[dict[str, Any]] = []
        tool_invocation_rows: list[dict[str, Any]] = []
        step_index = 0
        pending_tool_calls: list[dict] = []  # list of {"name": str, "args": dict}

        for msg in messages or []:
            if isinstance(msg, AIMessage):
                tool_calls = getattr(msg, "tool_calls", None) or []
                content_preview = _truncate_summary(getattr(msg, "content", None) or "")
                if tool_calls:
                    pending_tool_calls = [
                        {
                            "name": tc.get("name", "unknown"),
                            "args": tc.get("args") or {},
                        }
                        for tc in tool_calls
                    ]
                    trajectory_rows.append(
                        {
                            "ts": ts_str,
                            "request_id": request_id,
                            "session_id": session_id,
                            "step_index": step_index,
                            "step_type": "tool_call",
                            "tool_name": (
                                tool_calls[0].get("name")
                                if len(tool_calls) == 1
                                else None
                            ),
                            "input_summary": content_preview,
                            "output_summary": None,
                            "state_snapshot": None,
                            "duration_ms": None,
                            "error_message": None,
                            "metadata": [],
                        }
                    )
                    step_index += 1
                else:
                    trajectory_rows.append(
                        {
                            "ts": ts_str,
                            "request_id": request_id,
                            "session_id": session_id,
                            "step_index": step_index,
                            "step_type": "llm",
                            "tool_name": None,
                            "input_summary": None,
                            "output_summary": content_preview,
                            "state_snapshot": None,
                            "duration_ms": None,
                            "error_message": None,
                            "metadata": [],
                        }
                    )
                    step_index += 1

            elif isinstance(msg, ToolMessage):
                name = getattr(msg, "name", None) or "unknown"
                content = getattr(msg, "content", None) or ""
                input_args = ""
                if pending_tool_calls:
                    first = pending_tool_calls.pop(0)
                    input_args = _truncate_summary(str(first.get("args", "")))
                tool_invocation_rows.append(
                    {
                        "ts": ts_str,
                        "request_id": request_id,
                        "session_id": session_id,
                        "step_index": step_index,
                        "tool_name": name,
                        "input_summary": input_args or None,
                        "output_summary": _truncate_summary(content),
                        "success": True,
                        "duration_ms": None,
                        "error_message": None,
                    }
                )
                trajectory_rows.append(
                    {
                        "ts": ts_str,
                        "request_id": request_id,
                        "session_id": session_id,
                        "step_index": step_index,
                        "step_type": "tool_result",
                        "tool_name": name,
                        "input_summary": input_args or None,
                        "output_summary": _truncate_summary(content),
                        "state_snapshot": None,
                        "duration_ms": None,
                        "error_message": None,
                        "metadata": [],
                    }
                )
                step_index += 1

        return trajectory_rows, tool_invocation_rows

    def _write_trajectory_and_tool_invocations(
        self,
        messages: list,
        request_id: str,
        session_id: str | None,
        trajectory_rows: list[dict[str, Any]] | None = None,
        tool_invocation_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        """Write trajectory/tool rows from pre-built rows or parsed LangChain messages."""
        if trajectory_rows is None and tool_invocation_rows is None:
            trajectory_rows, tool_invocation_rows = (
                self._parse_messages_to_trajectory_and_tools(
                    messages, request_id, session_id
                )
            )
        else:
            trajectory_rows = trajectory_rows or []
            tool_invocation_rows = tool_invocation_rows or []
        # Always ensure tables exist (empty runs still leave discoverable tables in BQ).
        self._ensure_trajectory_table()
        self._ensure_tool_invocations_table()
        if not trajectory_rows and not tool_invocation_rows:
            if messages:
                try:
                    types = [type(m).__name__ for m in messages]
                    logger.info(
                        "Agent EVAL: no trajectory/tool rows for request_id=%s "
                        "(message types: %s). Rows are only emitted for AIMessage and "
                        "ToolMessage; agent_job_tracking / agent_quality_eval still write "
                        "from run metadata without parsing the message chain.",
                        request_id,
                        types,
                    )
                except Exception:
                    pass
            return
        if trajectory_rows:
            self._append_rows(self._trajectory_table_id(), trajectory_rows)
            logger.info(
                "Agent EVAL: wrote %d trajectory rows to %s",
                len(trajectory_rows),
                self._trajectory_table_id(),
            )
        if tool_invocation_rows:
            self._append_rows(self._tool_invocations_table_id(), tool_invocation_rows)
            logger.info(
                "Agent EVAL: wrote %d tool_invocation rows to %s",
                len(tool_invocation_rows),
                self._tool_invocations_table_id(),
            )


# Singleton for easy use from workflow
_default_client: TrackingClient | None = None


def get_tracking_client() -> TrackingClient:
    global _default_client
    if _default_client is None:
        _default_client = TrackingClient()
    return _default_client


def record_agent_run(
    request_id: str,
    session_id: str,
    input_text: str,
    output_text: str,
    start_ts: datetime | None = None,
    end_ts: datetime | None = None,
    experience_id: str | None = None,
    status: str | None = None,
    error_message: str | None = None,
    trace_id: str | None = None,
    **kwargs: Any,
) -> None:
    """
    Record one agent run to the generic agent_job_tracking table.
    Use from any agent (GraphQL, API, batch) so eval pipeline can ingest for quality measurement.
    """
    get_tracking_client().record_agent_run(
        request_id=request_id,
        session_id=session_id,
        input_text=input_text,
        output_text=output_text,
        start_ts=start_ts,
        end_ts=end_ts,
        experience_id=experience_id,
        status=status,
        error_message=error_message,
        trace_id=trace_id,
        **kwargs,
    )
