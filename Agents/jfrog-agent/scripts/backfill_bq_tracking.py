#!/usr/bin/env python3
"""Backfill recent audit runs into the active tracking backend (BigQuery)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv(override=True)

from jfrog_agent.settings import Settings  # noqa: E402
from jfrog_agent.tracking import get_tracking_backend  # noqa: E402
from jfrog_agent.tracking.run_recorder import record_jfrog_run  # noqa: E402


class AuditShim:
    def __init__(self, events: list):
        self.events = events


def backfill(audit_dir: Path, limit: int, dry_run: bool) -> int:
    settings = Settings()
    tracking = get_tracking_backend(settings)
    print(f"Backend: {type(tracking).__name__} -> {settings.bq_project_id}.{settings.bq_dataset_id}")

    files = sorted(audit_dir.glob("run-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    done = 0
    for path in files[:limit]:
        data = json.loads(path.read_text())
        run_id = data.get("run_id") or path.stem
        if run_id == "run-verify-201443":
            continue
        request = data.get("request", "")
        findings = []
        for ev in data.get("events", []):
            if ev.get("kind") == "tool_call":
                p = ev.get("payload", {})
                findings.append(
                    {
                        "tool": p.get("tool"),
                        "is_error": p.get("is_error"),
                        "intent": p.get("intent"),
                        "result": "error" if p.get("is_error") else "ok",
                    }
                )
        result = {
            "outcome": data.get("outcome", "completed"),
            "answer": f"(backfilled from {path.name})",
            "findings": findings,
            "run_id": run_id,
        }
        if dry_run:
            print(f"  would backfill {run_id}: {request[:60]}...")
            done += 1
            continue
        record_jfrog_run(
            tracking,
            run_id=run_id,
            thread_id=f"conv-backfill",
            request=request,
            result=result,
            audit=AuditShim(data.get("events", [])),
            settings=settings,
            start_ts=datetime.now(timezone.utc),
            end_ts=datetime.now(timezone.utc),
        )
        print(f"  backfilled {run_id}")
        done += 1
    print(f"Done: {done} runs")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-dir", default=os.path.expanduser("~/.jfrog-agent/audit"))
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    return backfill(Path(args.audit_dir), args.limit, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
