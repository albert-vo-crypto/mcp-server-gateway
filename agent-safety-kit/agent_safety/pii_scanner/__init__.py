"""
Post-write PII scanner for free-text records (offline / batch).

Use this *after* an agent has persisted telemetry. For blocking secrets
*before* the LLM, use ``agent_safety.input_guard``.
"""

from agent_safety.pii_scanner.detectors import PiiFinding, PiiType, scan_text
from agent_safety.pii_scanner.scanner import findings_summary, scan_records, scan_rows

__all__ = [
    "PiiFinding",
    "PiiType",
    "scan_text",
    "scan_rows",
    "scan_records",
    "findings_summary",
]
