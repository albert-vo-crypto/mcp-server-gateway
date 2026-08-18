"""
CLI for offline PII scanning (demo + reproducible journal examples).

Usage::

    python -m agent_safety.pii_scanner.entrypoint --demo
    python -m agent_safety.pii_scanner.entrypoint --demo --json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys


def _demo_rows() -> dict:
    return {
        "agent_job_tracking": [
            {
                "request_id": "demo-req-1",
                "input": "Show balances for jane.doe@example.com",
                "input_sanitized": "Show balances for jane.doe@example.com",
                "output": "Member SSN on file is 219-09-9999",
                "output_sanitized": None,
                "error_message": None,
            },
            {
                "request_id": "demo-req-clean",
                "input": "List available datasets",
                "input_sanitized": "List available datasets",
                "output": "Found 3 datasets",
                "output_sanitized": "Found 3 datasets",
                "error_message": None,
            },
        ],
        "agent_trajectory": [
            {
                "request_id": "demo-req-1",
                "input_summary": "call with card 4111-1111-1111-1111",
                "output_summary": "ok",
                "state_snapshot": None,
                "error_message": None,
            },
        ],
        "agent_tool_invocations": [],
        "agent_quality_eval": [],
        "agent_eval_scores": [],
        "agent_quality_metrics_detail": [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scan agent telemetry records for PII and alert."
    )
    parser.add_argument("--traffic-date", default=None)
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--no-alert", action="store_true")
    parser.add_argument("--demo", action="store_true", help="Use built-in sample rows")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not args.demo:
        print(
            "This CLI ships with --demo for offline reproduction.\n"
            "Pass your own records via agent_safety.pii_scanner.runner.scan_all_tables(...).",
            file=sys.stderr,
        )
        return 2

    from agent_safety.pii_scanner.runner import scan_all_tables

    report = scan_all_tables(
        traffic_date=args.traffic_date or "2026-08-01",
        dry_run=True,
        records=_demo_rows(),
        alert=not args.no_alert,
    )
    payload = report.to_dict()
    if args.as_json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        summary = payload["summary"]
        print("=" * 60)
        print("Agent Safety Kit — PII Scan Report")
        print("=" * 60)
        print(f"traffic_date : {summary['traffic_date']}")
        print(f"rows_scanned : {summary['rows_scanned']}")
        print(f"findings     : {summary['total_findings']}")
        print(f"by_type      : {summary['by_type']}")
        print(f"by_table     : {summary['by_table']}")
        for t in payload["tables"]:
            print(f"  {t['name']}: rows={t['rows_scanned']} findings={t['findings']}")
        print("=" * 60)
    return 1 if report.all_findings else 0


if __name__ == "__main__":
    sys.exit(main())
