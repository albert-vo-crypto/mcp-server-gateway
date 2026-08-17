"""BigQuery tracking/eval configuration (separate from agent memory)."""

from __future__ import annotations

import os

DEFAULT_TABLE_NAME = "agent_job_tracking"
QUALITY_EVAL_TABLE_NAME = os.getenv("AGENT_EVAL_QUALITY_TABLE_NAME", "agent_quality_eval")
TRAJECTORY_TABLE_NAME = os.getenv("AGENT_EVAL_TRAJECTORY_TABLE", "agent_trajectory")
TOOL_INVOCATIONS_TABLE_NAME = os.getenv("AGENT_EVAL_TOOL_INVOCATIONS_TABLE", "agent_tool_invocations")
EVAL_SCORES_TABLE_NAME = os.getenv("AGENT_EVAL_SCORES_TABLE", "agent_eval_scores")
QUALITY_METRICS_DETAIL_TABLE_NAME = os.getenv(
    "AGENT_EVAL_QUALITY_METRICS_DETAIL_TABLE", "agent_quality_metrics_detail"
)

BQ_TRACKING_PROJECT_ID = os.getenv("JFROG_AGENT_BQ_PROJECT_ID", "bqgcs-177117")
BQ_TRACKING_DATASET_ID = os.getenv("JFROG_AGENT_BQ_DATASET_ID", "agent_tracking_eval")
BQ_TRACKING_TABLE_NAME = os.getenv("JFROG_AGENT_BQ_TABLE_NAME", DEFAULT_TABLE_NAME)

# Written as agent_name in every BQ row
SERVICE_NAME = os.getenv("JFROG_AGENT_BQ_AGENT_NAME", "jfrog-langgraph-agent")
