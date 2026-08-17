"""
Generic BigQuery schema for agent eval tracking (all agent use cases).

Aligns with industry patterns: request tracing, timestamps, input/output,
status, optional cost/latency, and flexible metadata for eval pipelines.
Partitioned by ts, 90-day expiration. No vendor-specific columns.

Production metrics (task completion, tool accuracy, hallucination rate, mean
tool calls, P95 latency, cost per task, human override, regression) map mostly
to agent_eval_scores + agent_quality_metrics_detail + aggregates over
agent_job_tracking (duration_ms, tool rows), not as duplicate columns on every
table — see SCHEMA_AGENT_QUALITY_METRICS_DETAIL comment block below.
"""

import logging
import os

# BigQuery sandbox mode requires partition expiration < 60 days.
PARTITION_EXPIRATION_DAYS = int(os.getenv("JFROG_AGENT_BQ_PARTITION_DAYS", "59"))
from google.cloud import bigquery

logger = logging.getLogger(__name__)

# Patched onto older BigQuery tables that predate skill columns (nullable STRING).
_SKILL_SCHEMA_FIELDS: list[bigquery.SchemaField] = [
    bigquery.SchemaField("skill_directory", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("skill_name", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("skill_channel", "STRING", mode="NULLABLE"),
]


def patch_skill_columns_if_missing(client: bigquery.Client, table_id: str) -> None:
    """Append skill_directory / skill_name / skill_channel when missing (ALTER via update_table)."""
    try:
        table = client.get_table(table_id)
    except Exception:
        return
    have = {f.name for f in table.schema}
    additions = [f for f in _SKILL_SCHEMA_FIELDS if f.name not in have]
    if not additions:
        return
    table.schema = list(table.schema) + additions
    client.update_table(table, ["schema"])
    logger.info(
        "BigQuery schema updated for %s (added %s)",
        table_id,
        [f.name for f in additions],
    )


# Generic job tracking: one row per agent run (any agent in the company)
SCHEMA_AGENT_JOB_TRACKING = [
    bigquery.SchemaField("ts", "TIMESTAMP", mode="NULLABLE"),  # partition field
    bigquery.SchemaField("request_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("session_id", "STRING", mode="NULLABLE"),
    bigquery.SchemaField(
        "trace_id", "STRING", mode="NULLABLE"
    ),  # e.g. Langfuse trace ID
    bigquery.SchemaField(
        "agent_name", "STRING", mode="REQUIRED"
    ),  # e.g. bigquery-langgraph-agent
    bigquery.SchemaField("experience_id", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("input", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("input_sanitized", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("output", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("output_sanitized", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("status", "STRING", mode="NULLABLE"),  # complete, error
    bigquery.SchemaField("error_message", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("start_ts", "TIMESTAMP", mode="NULLABLE"),
    bigquery.SchemaField("end_ts", "TIMESTAMP", mode="NULLABLE"),
    bigquery.SchemaField("duration_ms", "INTEGER", mode="NULLABLE"),
    bigquery.SchemaField("model_id", "STRING", mode="NULLABLE"),
    bigquery.SchemaField(
        "skill_directory",
        "STRING",
        mode="NULLABLE",
    ),  # e.g. GraphQL skillDirectory when caller opted into baked skills
    bigquery.SchemaField(
        "skill_name",
        "STRING",
        mode="NULLABLE",
    ),  # display title from SKILL.md H1 when present, else folder id
    bigquery.SchemaField(
        "skill_channel",
        "STRING",
        mode="NULLABLE",
    ),  # e.g. CLAUDE / distribution label
    bigquery.SchemaField(
        "metadata",
        "RECORD",
        mode="REPEATED",
        fields=[
            bigquery.SchemaField("key", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("value", "STRING", mode="NULLABLE"),
        ],
    ),
]

# Backward compat alias; new code uses SCHEMA_AGENT_JOB_TRACKING
SCHEMA_GENAI_GUIDANCE_JOB_TRACKING = SCHEMA_AGENT_JOB_TRACKING

PARTITION_COLUMN = "ts"

# Quality eval tracking: one row per metric per request (evaluator output)
SCHEMA_QUALITY_EVAL = [
    bigquery.SchemaField("traffic_date", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("eval_run_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("request_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("agent_name", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("metric_name", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("result", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("explanation", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("metadata", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("ts", "TIMESTAMP", mode="NULLABLE"),
]
QUALITY_EVAL_PARTITION_COLUMN = "traffic_date"

# Recommended metric_name values for agent_quality_eval (what it measures → example)
QUALITY_EVAL_METRIC_NAMES = [
    "task_success_rate",  # goal achieved, e.g. refund processed correctly
    "tool_selection_accuracy",  # right tool per step, e.g. search_db vs delete_record
    "parameter_correctness",  # valid tool args, e.g. instance_id vs region_name
    "step_efficiency",  # minimal steps, e.g. 4 steps vs 12
    "safety_compliance",  # within policy, e.g. no unauthorized data access
    "cost",  # tokens/API per task, e.g. $0.03 per resolution
    "latency",  # input-to-completion time, e.g. 8s end-to-end
    "recovery_resilience",  # handled tool failures and adapted, e.g. retry with corrected params
]

# ---------------------------------------------------------------------------
# Trajectory: full run steps (decisions, tool calls, state changes across turns)
# One row per step in a run — for evaluating trajectory and model+scaffold+tools+env
# ---------------------------------------------------------------------------
SCHEMA_AGENT_TRAJECTORY = [
    bigquery.SchemaField("ts", "TIMESTAMP", mode="NULLABLE"),
    bigquery.SchemaField("request_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("session_id", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("step_index", "INTEGER", mode="REQUIRED"),  # 0-based turn/step
    bigquery.SchemaField(
        "step_type", "STRING", mode="NULLABLE"
    ),  # tool_call, tool_result, llm, state_change
    bigquery.SchemaField("tool_name", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("input_summary", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("output_summary", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("state_snapshot", "STRING", mode="NULLABLE"),  # JSON key state
    bigquery.SchemaField("duration_ms", "INTEGER", mode="NULLABLE"),
    bigquery.SchemaField("error_message", "STRING", mode="NULLABLE"),
    bigquery.SchemaField(
        "metadata",
        "RECORD",
        mode="REPEATED",
        fields=[
            bigquery.SchemaField("key", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("value", "STRING", mode="NULLABLE"),
        ],
    ),
]
TRAJECTORY_PARTITION_COLUMN = "ts"

# ---------------------------------------------------------------------------
# Tool invocations: one row per tool call (for tool accuracy, efficiency)
# ---------------------------------------------------------------------------
SCHEMA_AGENT_TOOL_INVOCATIONS = [
    bigquery.SchemaField("ts", "TIMESTAMP", mode="NULLABLE"),
    bigquery.SchemaField("request_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("session_id", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("step_index", "INTEGER", mode="NULLABLE"),
    bigquery.SchemaField("tool_name", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("input_summary", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("output_summary", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("success", "BOOLEAN", mode="NULLABLE"),
    bigquery.SchemaField("duration_ms", "INTEGER", mode="NULLABLE"),
    bigquery.SchemaField("error_message", "STRING", mode="NULLABLE"),
]
TOOL_INVOCATIONS_PARTITION_COLUMN = "ts"

# ---------------------------------------------------------------------------
# Multi-dimensional eval scores — aligned with common agent eval metrics:
#   1. Task success rate       → task_success_score
#   2. Tool selection accuracy → tool_accuracy_score
#   3. Parameter correctness   → parameter_correctness_score
#   4. Step efficiency         → efficiency_score
#   5. Safety and compliance   → safety_score
#   6. Cost                    → cost_score / cost_amount
#   7. Latency                 → latency_ms
#   8. Recovery and resilience → recovery_resilience_score
# Supports multiple trials per task: task_id + trial_index for aggregating across runs.
# ---------------------------------------------------------------------------
SCHEMA_AGENT_EVAL_SCORES = [
    bigquery.SchemaField("traffic_date", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("eval_run_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("request_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField(
        "task_id", "STRING", mode="NULLABLE"
    ),  # group trials: same prompt/task
    bigquery.SchemaField(
        "trial_index", "INTEGER", mode="NULLABLE"
    ),  # 0-based trial for this task
    bigquery.SchemaField("agent_name", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("skill_directory", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("skill_name", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("skill_channel", "STRING", mode="NULLABLE"),
    bigquery.SchemaField(
        "task_success_score", "FLOAT64", mode="NULLABLE"
    ),  # 1. goal achieved
    bigquery.SchemaField(
        "tool_accuracy_score", "FLOAT64", mode="NULLABLE"
    ),  # 2. right tool per step
    bigquery.SchemaField(
        "parameter_correctness_score", "FLOAT64", mode="NULLABLE"
    ),  # 3. valid args
    bigquery.SchemaField(
        "efficiency_score", "FLOAT64", mode="NULLABLE"
    ),  # 4. minimal steps
    bigquery.SchemaField(
        "safety_score", "FLOAT64", mode="NULLABLE"
    ),  # 5. within policy
    bigquery.SchemaField(
        "cost_score", "FLOAT64", mode="NULLABLE"
    ),  # 6. tokens/API cost
    bigquery.SchemaField(
        "latency_ms", "INTEGER", mode="NULLABLE"
    ),  # 7. input-to-completion time
    bigquery.SchemaField(
        "recovery_resilience_score", "FLOAT64", mode="NULLABLE"
    ),  # 8. handled failures
    bigquery.SchemaField("overall_score", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField(
        "dimensions_metadata", "STRING", mode="NULLABLE"
    ),  # JSON extra dimensions
    bigquery.SchemaField("ts", "TIMESTAMP", mode="NULLABLE"),
]
EVAL_SCORES_PARTITION_COLUMN = "traffic_date"

# ---------------------------------------------------------------------------
# In-depth quality metrics table (one row per run, nested STRUCTs for grouping).
# Partitioned by evaluated_date for cost-efficient querying. Populated by agent + evaluator.
#
# Overlap with common production dashboards:
#   - Task completion rate → agent_metrics.task_completion_rate (+ task_success_score in eval_scores)
#   - Tool call accuracy → agent_metrics.tool_call_accuracy (evaluator) + tool rows / invocations
#   - Hallucination rate → safety_alignment.hallucination_flag + evaluator / batch labels
#   - Mean tool calls → agent_metrics.tool_call_count (per run; rollups = AVG in SQL)
#   - P95 latency → percentile over latency_cost.latency_ms (or agent_job_tracking.duration_ms)
#   - Cost per task → latency_cost.estimated_cost_usd (when wired) or token-based estimates
#   - Human override rate → not a per-run agent field; add via evaluator or ops metadata when available
#   - Regression rate → eval harness compares runs; aggregate, not one column per job row
# ---------------------------------------------------------------------------
SCHEMA_AGENT_QUALITY_METRICS_DETAIL = [
    # Metadata (always include)
    bigquery.SchemaField("evaluated_date", "DATE", mode="REQUIRED"),  # partition column
    bigquery.SchemaField("run_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("session_id", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("model_name", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("prompt_version", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("evaluated_at", "TIMESTAMP", mode="NULLABLE"),
    bigquery.SchemaField(
        "evaluator_type", "STRING", mode="NULLABLE"
    ),  # llm, human, heuristic
    bigquery.SchemaField("agent_name", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("skill_directory", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("skill_name", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("skill_channel", "STRING", mode="NULLABLE"),
    # Response Quality (e.g. RAGAS-style)
    bigquery.SchemaField(
        "response_quality",
        "RECORD",
        mode="NULLABLE",
        fields=[
            bigquery.SchemaField("faithfulness_score", "FLOAT64", mode="NULLABLE"),
            bigquery.SchemaField("answer_relevance_score", "FLOAT64", mode="NULLABLE"),
            bigquery.SchemaField("context_precision_score", "FLOAT64", mode="NULLABLE"),
            bigquery.SchemaField("context_recall_score", "FLOAT64", mode="NULLABLE"),
        ],
    ),
    # Agent-Specific
    bigquery.SchemaField(
        "agent_metrics",
        "RECORD",
        mode="NULLABLE",
        fields=[
            bigquery.SchemaField("task_completion_rate", "FLOAT64", mode="NULLABLE"),
            bigquery.SchemaField("tool_call_accuracy", "FLOAT64", mode="NULLABLE"),
            bigquery.SchemaField("tool_call_count", "INT64", mode="NULLABLE"),
            bigquery.SchemaField("step_count", "INT64", mode="NULLABLE"),
            bigquery.SchemaField("redundant_steps", "INT64", mode="NULLABLE"),
        ],
    ),
    # Latency & Cost
    bigquery.SchemaField(
        "latency_cost",
        "RECORD",
        mode="NULLABLE",
        fields=[
            bigquery.SchemaField("latency_ms", "INT64", mode="NULLABLE"),
            bigquery.SchemaField("time_to_first_token_ms", "INT64", mode="NULLABLE"),
            bigquery.SchemaField("input_tokens", "INT64", mode="NULLABLE"),
            bigquery.SchemaField("output_tokens", "INT64", mode="NULLABLE"),
            bigquery.SchemaField("estimated_cost_usd", "FLOAT64", mode="NULLABLE"),
        ],
    ),
    # Safety & Alignment
    bigquery.SchemaField(
        "safety_alignment",
        "RECORD",
        mode="NULLABLE",
        fields=[
            bigquery.SchemaField("hallucination_flag", "BOOL", mode="NULLABLE"),
            bigquery.SchemaField("toxicity_score", "FLOAT64", mode="NULLABLE"),
            bigquery.SchemaField("refusal_flag", "BOOL", mode="NULLABLE"),
            bigquery.SchemaField("pii_detected", "BOOL", mode="NULLABLE"),
        ],
    ),
    # Multi-turn agent trace (REPEATED)
    bigquery.SchemaField(
        "trace",
        "RECORD",
        mode="REPEATED",
        fields=[
            bigquery.SchemaField("step_index", "INT64", mode="NULLABLE"),
            bigquery.SchemaField("step_type", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("tool_name", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("duration_ms", "INT64", mode="NULLABLE"),
            bigquery.SchemaField("error_message", "STRING", mode="NULLABLE"),
        ],
    ),
]
QUALITY_METRICS_DETAIL_PARTITION_COLUMN = "evaluated_date"

_QUALITY_METRICS_DETAIL_NESTED_NAMES = frozenset(
    {
        "response_quality",
        "agent_metrics",
        "latency_cost",
        "safety_alignment",
        "trace",
    }
)


def patch_quality_metrics_detail_nested_columns_if_missing(
    client: bigquery.Client, table_id: str
) -> None:
    """Align legacy agent_quality_metrics_detail with nested fields from SCHEMA.

    Uses ``tables.update`` (schema patch) instead of DDL ``ALTER TABLE`` jobs so workloads
    that lack ``bigquery.jobs.create`` but have ``bigquery.tables.update`` can still migrate.
    """
    try:
        table = client.get_table(table_id)
    except Exception:
        return
    have = {f.name for f in table.schema}
    additions = [
        f
        for f in SCHEMA_AGENT_QUALITY_METRICS_DETAIL
        if f.name in _QUALITY_METRICS_DETAIL_NESTED_NAMES and f.name not in have
    ]
    if not additions:
        return
    try:
        table.schema = list(table.schema) + additions
        client.update_table(table, ["schema"])
        logger.info(
            "BigQuery schema updated for %s (added nested columns %s)",
            table_id,
            [f.name for f in additions],
        )
    except Exception as e:
        logger.warning(
            "Could not patch nested columns on %s (grant bigquery.tables.update on the "
            "table or run equivalent DDL manually): %s",
            table_id,
            e,
        )
