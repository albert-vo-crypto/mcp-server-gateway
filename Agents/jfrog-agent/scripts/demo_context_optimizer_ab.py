#!/usr/bin/env python3
"""
Run ONE JFrog agent task for context-optimizer A/B demo.

Usage (from Agents/jfrog-agent/, gateway running, OAuth token cached):

  # WITHOUT context layer
  CTX_OPT_ENABLED=false python scripts/demo_context_optimizer_ab.py --condition no_layer

  # WITH context layer (fixed budget)
  CTX_OPT_ENABLED=true CTX_OPT_PRESET=balanced CTX_OPT_MAX_TOKENS=8000 \\
    python scripts/demo_context_optimizer_ab.py --condition with_layer

Session IDs are prefixed (no_layer-… / with_layer-…) so BigQuery SQL can compare runs.
Watch stdout for per-LLM-call lines: ctx-opt[balanced] saved: X -> Y tokens
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(override=True)

DEFAULT_QUERY = (
    "List all local repositories, then search for docker packages larger than 1GB "
    "that have not been downloaded in 90 days. Summarize what you find."
)

BQ_COMPARE_SQL = """
-- Paste after both runs (adjust project/dataset if needed)
SELECT
  CASE
    WHEN STARTS_WITH(session_id, 'no_layer-') THEN 'no_layer'
    WHEN STARTS_WITH(session_id, 'with_layer-') THEN 'with_layer'
    ELSE 'other'
  END AS condition,
  COUNT(*) AS runs,
  COUNTIF(status = 'complete') AS completed,
  ROUND(COUNTIF(status = 'complete') / COUNT(*), 3) AS completion_rate,
  ROUND(AVG(duration_ms)) AS avg_duration_ms,
  ROUND(AVG(CAST((SELECT value FROM UNNEST(metadata) WHERE key = 'input_tokens') AS INT64))) AS avg_input_tokens,
  ROUND(AVG(CAST((SELECT value FROM UNNEST(metadata) WHERE key = 'total_tokens') AS INT64))) AS avg_total_tokens,
  ROUND(AVG(CAST((SELECT value FROM UNNEST(metadata) WHERE key = 'llm_call_count') AS INT64)), 1) AS avg_llm_calls,
  ROUND(AVG(CAST((SELECT value FROM UNNEST(metadata) WHERE key = 'ctx_opt_tokens_saved') AS INT64))) AS avg_ctx_saved
FROM `{project}.{dataset}.agent_job_tracking`
WHERE STARTS_WITH(session_id, 'no_layer-') OR STARTS_WITH(session_id, 'with_layer-')
GROUP BY 1
ORDER BY 1;
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Context optimizer A/B demo (JFrog agent, single run)")
    parser.add_argument(
        "--condition",
        required=True,
        choices=["no_layer", "with_layer"],
        help="Label written into session_id for BQ comparison",
    )
    parser.add_argument("--query", default=DEFAULT_QUERY, help="Task to run (same for both arms)")
    args = parser.parse_args()

    ctx_on = os.environ.get("CTX_OPT_ENABLED", "").lower() in {"1", "true", "yes", "on"}
    preset = os.environ.get("CTX_OPT_PRESET", "balanced")
    max_tok = os.environ.get("CTX_OPT_MAX_TOKENS", "8000")

    thread_id = f"{args.condition}-{int(time.time())}"
    print("=" * 60)
    print(f"Condition label : {args.condition}")
    print(f"CTX_OPT_ENABLED : {ctx_on}")
    if ctx_on:
        print(f"CTX_OPT_PRESET  : {preset}")
        print(f"CTX_OPT_MAX_TOKENS: {max_tok}")
    print(f"session_id      : {thread_id}")
    print(f"Query           : {args.query[:100]}...")
    print("=" * 60)

    from jfrog_agent import telemetry
    from jfrog_agent.audit_log import AuditTrail, new_run_id
    from jfrog_agent.context_optimizer import clear_optimizer_cache
    from jfrog_agent.context_optimizer.stats import get_run_stats, reset_run_stats
    from jfrog_agent.graph import build_graph
    from jfrog_agent.mcp_client import AuthError, GatewayMCPClient
    from jfrog_agent.settings import Settings
    from jfrog_agent.tracking import get_tracking_backend
    from jfrog_agent.tracking.run_recorder import record_jfrog_run

    settings = Settings()
    clear_optimizer_cache()
    reset_run_stats()

    client = GatewayMCPClient(settings, verbose=True)
    try:
        client.connect()
    except AuthError as exc:
        print(f"Auth error: {exc}", file=sys.stderr)
        print("Tip: run `python run.py --login` first.", file=sys.stderr)
        return 2

    run_id = new_run_id()
    audit = AuditTrail(run_id, settings)
    graph = build_graph(client, audit, settings)
    config = {"configurable": {"thread_id": run_id}}
    telemetry.set_context(run_id=run_id, thread_id=thread_id)

    start_ts = datetime.now(timezone.utc)
    result = graph.invoke({"request": args.query, "user": "demo", "run_id": run_id}, config)
    end_ts = datetime.now(timezone.utc)

    tracking = get_tracking_backend(settings)
    if tracking:
        record_jfrog_run(
            tracking,
            run_id=run_id,
            thread_id=thread_id,
            request=args.query,
            result=result,
            audit=audit,
            settings=settings,
            start_ts=start_ts,
            end_ts=end_ts,
        )

    print("\n--- Run result ---")
    print(f"outcome    : {result.get('outcome')}")
    print(f"request_id : {run_id}")
    print(f"session_id : {thread_id}")
    print(f"ctx stats  : {get_run_stats()}")
    if result.get("answer"):
        print(f"\nAnswer preview:\n{str(result.get('answer'))[:500]}...")

    project = settings.bq_project_id
    dataset = settings.bq_dataset_id
    print("\n--- BigQuery compare query (run after BOTH arms) ---")
    print(BQ_COMPARE_SQL.format(project=project, dataset=dataset))
    print("\n--- Per-run detail ---")
    print(
        f"SELECT request_id, session_id, status, duration_ms,\n"
        f"  (SELECT value FROM UNNEST(metadata) WHERE key='input_tokens') AS input_tokens,\n"
        f"  (SELECT value FROM UNNEST(metadata) WHERE key='total_tokens') AS total_tokens,\n"
        f"  (SELECT value FROM UNNEST(metadata) WHERE key='ctx_opt_tokens_saved') AS ctx_saved,\n"
        f"  input\n"
        f"FROM `{project}.{dataset}.agent_job_tracking`\n"
        f"WHERE session_id = '{thread_id}';"
    )
    print("\n--- Log evidence (before→after per LLM call; WITH layer only) ---")
    print("  Look for lines starting with `ctx-opt[` in stdout above.")
    client.close()
    return 0 if (result.get("outcome") or "").lower() == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
