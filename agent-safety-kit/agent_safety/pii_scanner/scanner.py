"""Row-level PII scanning over dict-like records."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from agent_safety.pii_scanner.detectors import DEFAULT_DETECTORS, Detector, PiiFinding, scan_text
from agent_safety.pii_scanner.registry import TableScanTarget


def _as_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        parts = []
        for item in value:
            if isinstance(item, Mapping):
                parts.append(f"{item.get('key', '')}={item.get('value', '')}")
            else:
                parts.append(str(item))
        return "\n".join(parts) if parts else None
    if isinstance(value, Mapping):
        return str(dict(value))
    return str(value)


def scan_row(
    row: Mapping[str, Any],
    *,
    text_columns: Sequence[str],
    table: str | None = None,
    request_id_column: str = "request_id",
    detectors: Iterable[Detector] = DEFAULT_DETECTORS,
) -> List[PiiFinding]:
    request_id = row.get(request_id_column)
    if request_id is not None:
        request_id = str(request_id)
    findings: List[PiiFinding] = []
    for col in text_columns:
        text = _as_str(row.get(col))
        if not text:
            continue
        findings.extend(
            scan_text(
                text,
                detectors=detectors,
                column=col,
                request_id=request_id,
                table=table,
                row_key=request_id,
            )
        )
    return findings


def scan_rows(
    rows: Iterable[Mapping[str, Any]],
    target: TableScanTarget,
    *,
    detectors: Iterable[Detector] = DEFAULT_DETECTORS,
) -> List[PiiFinding]:
    all_findings: List[PiiFinding] = []
    for row in rows:
        all_findings.extend(
            scan_row(
                row,
                text_columns=target.text_columns,
                table=target.name,
                request_id_column=target.request_id_column,
                detectors=detectors,
            )
        )
    return all_findings


def scan_records(
    records_by_table: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    targets: Sequence[TableScanTarget] | None = None,
) -> List[PiiFinding]:
    """
    Scan an in-memory multi-table snapshot (no warehouse required).

    ``records_by_table`` maps logical table name → list of row dicts.
    """
    from agent_safety.pii_scanner.registry import default_scan_targets

    out: List[PiiFinding] = []
    by_name = {t.name: t for t in (targets or default_scan_targets())}
    for name, rows in records_by_table.items():
        target = by_name.get(name)
        if target is None or not target.enabled:
            # Ad-hoc: scan all string values if table not in registry
            for row in rows:
                cols = [k for k, v in row.items() if isinstance(v, str)]
                out.extend(
                    scan_row(
                        row,
                        text_columns=cols,
                        table=name,
                        request_id_column="request_id",
                    )
                )
            continue
        out.extend(scan_rows(rows, target))
    return out


def findings_summary(findings: Sequence[PiiFinding]) -> Dict[str, Any]:
    by_type: Dict[str, int] = {}
    by_table: Dict[str, int] = {}
    max_severity = 0
    request_ids: set[str] = set()
    for f in findings:
        by_type[f.pii_type.value] = by_type.get(f.pii_type.value, 0) + 1
        t = f.table or "unknown"
        by_table[t] = by_table.get(t, 0) + 1
        max_severity = max(max_severity, f.severity)
        if f.request_id:
            request_ids.add(f.request_id)
    return {
        "total_findings": len(findings),
        "by_type": by_type,
        "by_table": by_table,
        "max_severity": max_severity,
        "affected_request_ids": sorted(request_ids),
        "affected_request_count": len(request_ids),
    }
