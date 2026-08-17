#!/usr/bin/env python3
"""Create BigQuery tracking/eval tables for the JFrog agent.

Uses the `bq` CLI (gcloud user credentials) so `gcloud config set account
xyz@test.com` is respected. Falls back to the Python TrackingClient when
--python is passed (requires Application Default Credentials).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile

# Allow running from repo root without install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from jfrog_agent.tracking.config import (  # noqa: E402
    BQ_TRACKING_TABLE_NAME,
    EVAL_SCORES_TABLE_NAME,
    QUALITY_EVAL_TABLE_NAME,
    QUALITY_METRICS_DETAIL_TABLE_NAME,
    TOOL_INVOCATIONS_TABLE_NAME,
    TRAJECTORY_TABLE_NAME,
)
from jfrog_agent.tracking.table_schema import (  # noqa: E402
    EVAL_SCORES_PARTITION_COLUMN,
    PARTITION_COLUMN,
    PARTITION_EXPIRATION_DAYS,
    QUALITY_EVAL_PARTITION_COLUMN,
    QUALITY_METRICS_DETAIL_PARTITION_COLUMN,
    SCHEMA_AGENT_EVAL_SCORES,
    SCHEMA_AGENT_JOB_TRACKING,
    SCHEMA_AGENT_QUALITY_METRICS_DETAIL,
    SCHEMA_AGENT_TOOL_INVOCATIONS,
    SCHEMA_AGENT_TRAJECTORY,
    SCHEMA_QUALITY_EVAL,
    TOOL_INVOCATIONS_PARTITION_COLUMN,
    TRAJECTORY_PARTITION_COLUMN,
)


def _bq_mk(project: str, dataset: str, table: str, schema, partition_field: str, with_expiration: bool) -> None:
    payload = json.dumps([f.to_api_repr() for f in schema])
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        f.write(payload)
        path = f.name
    try:
        cmd = [
            "bq",
            "mk",
            "--table",
            "--time_partitioning_field",
            partition_field,
            "--time_partitioning_type",
            "DAY",
        ]
        if with_expiration:
            cmd += ["--time_partitioning_expiration", str(PARTITION_EXPIRATION_DAYS * 24 * 60 * 60)]
        cmd += [f"{project}:{dataset}.{table}", path]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or exc.stdout or "").strip()
        if "Already Exists" in err:
            print(f"  exists: {table}")
            return
        raise RuntimeError(f"bq mk failed for {table}: {err}") from exc
    finally:
        os.unlink(path)
    print(f"  created: {table}")


def create_via_bq_cli(project: str, dataset: str) -> None:
    tables = [
        (BQ_TRACKING_TABLE_NAME, SCHEMA_AGENT_JOB_TRACKING, PARTITION_COLUMN, True),
        (QUALITY_EVAL_TABLE_NAME, SCHEMA_QUALITY_EVAL, QUALITY_EVAL_PARTITION_COLUMN, False),
        (EVAL_SCORES_TABLE_NAME, SCHEMA_AGENT_EVAL_SCORES, EVAL_SCORES_PARTITION_COLUMN, False),
        (
            QUALITY_METRICS_DETAIL_TABLE_NAME,
            SCHEMA_AGENT_QUALITY_METRICS_DETAIL,
            QUALITY_METRICS_DETAIL_PARTITION_COLUMN,
            False,
        ),
        (TRAJECTORY_TABLE_NAME, SCHEMA_AGENT_TRAJECTORY, TRAJECTORY_PARTITION_COLUMN, True),
        (TOOL_INVOCATIONS_TABLE_NAME, SCHEMA_AGENT_TOOL_INVOCATIONS, TOOL_INVOCATIONS_PARTITION_COLUMN, True),
    ]
    acct = subprocess.run(["gcloud", "config", "get-value", "account"], capture_output=True, text=True, check=True)
    print(f"Using gcloud account: {acct.stdout.strip()}")
    for name, schema, part, with_exp in tables:
        _bq_mk(project, dataset, name, schema, part, with_exp)


def create_via_python_client(project: str, dataset: str) -> None:
    from jfrog_agent.tracking.bq_client import TrackingClient

    client = TrackingClient(project_id=project, dataset_id=dataset)
    client.create_table_if_not_exists()
    client._ensure_quality_eval_table()
    client._ensure_eval_scores_table()
    client._ensure_quality_metrics_detail_table()
    client._ensure_trajectory_table()
    client._ensure_tool_invocations_table()


def main() -> int:
    parser = argparse.ArgumentParser(description="Create JFrog agent BigQuery tracking tables")
    parser.add_argument("--project", default=os.getenv("JFROG_AGENT_BQ_PROJECT_ID", "bqgcs-177117"))
    parser.add_argument("--dataset", default=os.getenv("JFROG_AGENT_BQ_DATASET_ID", "agent_tracking_eval"))
    parser.add_argument(
        "--python",
        action="store_true",
        help="Use Python BigQuery client (ADC) instead of bq CLI (gcloud account)",
    )
    args = parser.parse_args()

    if args.python:
        create_via_python_client(args.project, args.dataset)
    else:
        create_via_bq_cli(args.project, args.dataset)

    base = f"{args.project}.{args.dataset}"
    print("Tables in dataset:")
    for name in (
        BQ_TRACKING_TABLE_NAME,
        QUALITY_EVAL_TABLE_NAME,
        EVAL_SCORES_TABLE_NAME,
        QUALITY_METRICS_DETAIL_TABLE_NAME,
        TRAJECTORY_TABLE_NAME,
        TOOL_INVOCATIONS_TABLE_NAME,
    ):
        print(f"  - {base}.{name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
