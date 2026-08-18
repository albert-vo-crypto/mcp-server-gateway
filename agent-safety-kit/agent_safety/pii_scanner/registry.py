"""
Default table / column registry for agent telemetry stores.

These are *logical* names — map them to your warehouse tables.
Readers can edit this list to match their own schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class TableScanTarget:
    """One logical table + free-text columns to scan."""

    name: str
    text_columns: List[str]
    request_id_column: str = "request_id"
    date_column: Optional[str] = "ts"
    enabled: bool = True


def default_scan_targets() -> List[TableScanTarget]:
    """Generic agent tracking / eval tables used in the book examples."""
    return [
        TableScanTarget(
            name="agent_job_tracking",
            text_columns=[
                "input",
                "input_sanitized",
                "output",
                "output_sanitized",
                "error_message",
            ],
        ),
        TableScanTarget(
            name="agent_trajectory",
            text_columns=[
                "input_summary",
                "output_summary",
                "state_snapshot",
                "error_message",
            ],
        ),
        TableScanTarget(
            name="agent_tool_invocations",
            text_columns=["input_summary", "output_summary", "error_message"],
        ),
        TableScanTarget(
            name="agent_quality_eval",
            text_columns=["result", "explanation", "metadata"],
            date_column="traffic_date",
        ),
        TableScanTarget(
            name="agent_eval_scores",
            text_columns=["dimensions_metadata"],
            date_column="traffic_date",
        ),
        TableScanTarget(
            name="agent_quality_metrics_detail",
            text_columns=["prompt_version", "agent_name"],
            request_id_column="run_id",
            date_column="evaluated_date",
        ),
    ]
