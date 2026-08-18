"""
Alerting for PII findings (stdlib only).

Channels:
  * structured logging (always)
  * optional webhook via ``PII_ALERT_WEBHOOK_URL``
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from agent_safety.pii_scanner.detectors import PiiFinding
from agent_safety.pii_scanner.scanner import findings_summary

logger = logging.getLogger(__name__)


@dataclass
class AlertResult:
    logged: bool = False
    webhook_sent: bool = False
    webhook_error: Optional[str] = None
    suppressed: bool = False
    details: Dict[str, Any] = field(default_factory=dict)


def _min_severity() -> int:
    try:
        return int(os.environ.get("PII_ALERT_MIN_SEVERITY", "50").strip())
    except ValueError:
        return 50


def _filter(findings: Sequence[PiiFinding]) -> List[PiiFinding]:
    t = _min_severity()
    return [f for f in findings if f.severity >= t]


def build_alert_payload(
    findings: Sequence[PiiFinding],
    *,
    traffic_date: str,
    dry_run: bool = False,
) -> Dict[str, Any]:
    actionable = _filter(findings)
    summary = findings_summary(actionable)
    return {
        "alert_type": "agent_pii_detected",
        "traffic_date": traffic_date,
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "summary": summary,
        "sample_findings": [f.as_dict() for f in actionable[:25]],
        "message": (
            f"PII scan found {summary['total_findings']} finding(s) across "
            f"{summary['affected_request_count']} request(s) on {traffic_date}. "
            f"Types: {summary['by_type']}"
        ),
    }


def send_webhook(payload: Dict[str, Any], url: str, timeout: float = 10.0) -> None:
    body = {**payload, "text": payload.get("message", "PII detected")}
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status >= 400:
            raise RuntimeError(f"webhook HTTP {resp.status}")


def emit_alerts(
    findings: Sequence[PiiFinding],
    *,
    traffic_date: str,
    dry_run: bool = False,
) -> AlertResult:
    result = AlertResult()
    actionable = _filter(findings)
    if not actionable:
        result.suppressed = True
        logger.info(
            "PII alert: no findings above severity=%s (raw=%d)",
            _min_severity(),
            len(findings),
        )
        return result

    payload = build_alert_payload(
        actionable, traffic_date=traffic_date, dry_run=dry_run
    )
    result.details = payload["summary"]
    logger.warning("PII_ALERT %s", json.dumps(payload, default=str))
    result.logged = True

    if dry_run:
        return result

    url = (os.environ.get("PII_ALERT_WEBHOOK_URL") or "").strip()
    if url:
        try:
            send_webhook(payload, url)
            result.webhook_sent = True
        except (urllib.error.URLError, TimeoutError, RuntimeError, OSError) as exc:
            result.webhook_error = str(exc)
            logger.error("PII alert webhook failed: %s", exc)
    return result
