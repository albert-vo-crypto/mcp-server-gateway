#!/usr/bin/env python3
"""Example 3 — offline PII scan of agent telemetry rows (no cloud required)."""

from agent_safety.pii_scanner.runner import scan_all_tables

records = {
    "agent_job_tracking": [
        {
            "request_id": "req-100",
            "input": "Show history for jane.doe@example.com",
            "output": "SSN on file 219-09-9999",
            "error_message": None,
        }
    ],
    "agent_trajectory": [
        {
            "request_id": "req-100",
            "input_summary": "paid with 4111-1111-1111-1111",
            "output_summary": "ok",
        }
    ],
}

report = scan_all_tables(
    traffic_date="2026-08-01",
    dry_run=True,
    records=records,
    alert=True,
)

print(report.to_dict()["summary"])
for f in report.all_findings:
    print(
        f"  {f.pii_type.value:12} table={f.table} col={f.column} "
        f"req={f.request_id} match={f.matched}"
    )
