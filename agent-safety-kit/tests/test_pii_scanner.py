"""Tests for post-write PII scanner (offline)."""

from __future__ import annotations

import json

from agent_safety.pii_scanner import scan_records, scan_text
from agent_safety.pii_scanner.alerter import build_alert_payload, emit_alerts
from agent_safety.pii_scanner.detectors import PiiType
from agent_safety.pii_scanner.entrypoint import main as pii_main
from agent_safety.pii_scanner.registry import default_scan_targets
from agent_safety.pii_scanner.runner import scan_all_tables


def test_scan_text_email():
    findings = scan_text("Contact jane.doe@example.com please")
    assert len(findings) == 1
    assert findings[0].pii_type == PiiType.EMAIL
    assert "jane.doe@example.com" not in findings[0].matched


def test_scan_text_ssn():
    assert any(f.pii_type == PiiType.SSN for f in scan_text("Member SSN 219-09-9999"))


def test_scan_text_rejects_invalid_ssn_area():
    assert not any(f.pii_type == PiiType.SSN for f in scan_text("SSN 000-12-3456"))


def test_scan_text_credit_card_luhn():
    assert any(
        f.pii_type == PiiType.CREDIT_CARD
        for f in scan_text("card 4111-1111-1111-1111 used")
    )
    assert not any(
        f.pii_type == PiiType.CREDIT_CARD
        for f in scan_text("card 4111-1111-1111-1112 used")
    )


def test_scan_text_clean_returns_empty():
    assert scan_text("List datasets in my analytics project") == []


def test_registry_covers_core_tables():
    names = {t.name for t in default_scan_targets()}
    assert "agent_job_tracking" in names
    assert "agent_trajectory" in names


def test_scan_records_offline():
    findings = scan_records(
        {
            "agent_job_tracking": [
                {
                    "request_id": "r1",
                    "input": "help alice@example.com",
                    "output": "ok",
                }
            ]
        }
    )
    assert any(f.pii_type == PiiType.EMAIL for f in findings)
    assert findings[0].request_id == "r1"


def test_build_alert_payload_redacted_only():
    findings = scan_text(
        "ssn 219-09-9999",
        column="output",
        request_id="r1",
        table="agent_job_tracking",
    )
    payload = build_alert_payload(findings, traffic_date="2026-08-01", dry_run=True)
    assert "219-09-9999" not in json.dumps(payload)


def test_emit_alerts_dry_run(monkeypatch):
    monkeypatch.setenv("PII_ALERT_MIN_SEVERITY", "1")
    findings = scan_text("alice@example.com", table="t", request_id="r")
    result = emit_alerts(findings, traffic_date="2026-08-01", dry_run=True)
    assert result.logged is True
    assert result.webhook_sent is False


def test_scan_all_tables_demo():
    report = scan_all_tables(
        traffic_date="2026-08-01",
        dry_run=True,
        records={
            "agent_job_tracking": [
                {
                    "request_id": "demo-1",
                    "input": "help jane.doe@example.com",
                    "output": "SSN 219-09-9999",
                }
            ],
            "agent_trajectory": [
                {
                    "request_id": "demo-1",
                    "input_summary": "card 4111-1111-1111-1111",
                    "output_summary": "ok",
                }
            ],
        },
        alert=True,
    )
    types = {f.pii_type for f in report.all_findings}
    assert PiiType.EMAIL in types
    assert PiiType.SSN in types
    assert PiiType.CREDIT_CARD in types
    assert report.alert and report.alert.logged


def test_cli_demo_exits_nonzero():
    assert pii_main(["--demo", "--json"]) == 1
