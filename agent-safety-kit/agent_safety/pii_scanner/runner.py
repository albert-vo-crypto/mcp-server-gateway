"""Orchestrate offline (in-memory) PII scans across registered tables."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

from agent_safety.pii_scanner.alerter import AlertResult, emit_alerts
from agent_safety.pii_scanner.detectors import PiiFinding
from agent_safety.pii_scanner.registry import TableScanTarget, default_scan_targets
from agent_safety.pii_scanner.scanner import findings_summary, scan_rows

logger = logging.getLogger(__name__)


@dataclass
class TableScanResult:
    target: TableScanTarget
    rows_scanned: int = 0
    findings: List[PiiFinding] = field(default_factory=list)
    skipped: bool = False


@dataclass
class ScanReport:
    traffic_date: str
    tables: List[TableScanResult] = field(default_factory=list)
    alert: Optional[AlertResult] = None
    dry_run: bool = False

    @property
    def all_findings(self) -> List[PiiFinding]:
        out: List[PiiFinding] = []
        for t in self.tables:
            out.extend(t.findings)
        return out

    def summary(self) -> Dict[str, Any]:
        s = findings_summary(self.all_findings)
        s["traffic_date"] = self.traffic_date
        s["rows_scanned"] = sum(t.rows_scanned for t in self.tables)
        s["dry_run"] = self.dry_run
        return s

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary": self.summary(),
            "tables": [
                {
                    "name": t.target.name,
                    "rows_scanned": t.rows_scanned,
                    "findings": len(t.findings),
                    "skipped": t.skipped,
                }
                for t in self.tables
            ],
            "alert": {
                "logged": bool(self.alert and self.alert.logged),
                "webhook_sent": bool(self.alert and self.alert.webhook_sent),
                "suppressed": bool(self.alert and self.alert.suppressed),
            },
        }


def resolve_traffic_date(traffic_date: str | date | None = None) -> str:
    if traffic_date is None:
        return (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    if isinstance(traffic_date, date):
        return traffic_date.isoformat()
    return str(traffic_date).strip()


def scan_all_tables(
    *,
    traffic_date: str | date | None = None,
    dry_run: bool = True,
    records: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    targets: Sequence[TableScanTarget] | None = None,
    alert: bool = True,
) -> ScanReport:
    """
    Scan in-memory records for every registered table.

    Pass ``records`` as ``{table_name: [row_dict, ...]}``. This is the
    journal-friendly path — no cloud warehouse required.
    """
    td = resolve_traffic_date(traffic_date)
    report = ScanReport(traffic_date=td, dry_run=dry_run)
    records = records or {}
    for target in targets or default_scan_targets():
        result = TableScanResult(target=target)
        if not target.enabled:
            result.skipped = True
            report.tables.append(result)
            continue
        rows = list(records.get(target.name, []))
        result.rows_scanned = len(rows)
        result.findings = scan_rows(rows, target)
        logger.info(
            "PII scan %s: rows=%d findings=%d",
            target.name,
            result.rows_scanned,
            len(result.findings),
        )
        report.tables.append(result)

    if alert:
        report.alert = emit_alerts(
            report.all_findings, traffic_date=td, dry_run=dry_run
        )
    return report
