"""
Context cleaning: redact secrets and PII so the LLM never sees raw values.

Replacement tokens are typed so the model keeps intent
("user asked about [REDACTED_EMAIL]") without the secret.
"""

from __future__ import annotations

import re
from typing import List, Sequence, Tuple

from agent_safety.input_guard.secrets import SecretFinding, scan_secrets
from agent_safety.pii_scanner.detectors import scan_text as scan_pii

_PII_TOKEN = {
    "email": "[REDACTED_EMAIL]",
    "ssn": "[REDACTED_SSN]",
    "phone": "[REDACTED_PHONE]",
    "credit_card": "[REDACTED_CREDIT_CARD]",
    "ipv4": "[REDACTED_IP]",
    "us_passport": "[REDACTED_PASSPORT]",
    "date_of_birth": "[REDACTED_DOB]",
}

_SECRET_TOKEN = {
    "aws_access_key": "[REDACTED_AWS_ACCESS_KEY]",
    "aws_secret_key": "[REDACTED_AWS_SECRET]",
    "gcp_api_key": "[REDACTED_GCP_API_KEY]",
    "github_token": "[REDACTED_GITHUB_TOKEN]",
    "slack_token": "[REDACTED_SLACK_TOKEN]",
    "openai_key": "[REDACTED_OPENAI_KEY]",
    "stripe_key": "[REDACTED_STRIPE_KEY]",
    "private_key_block": "[REDACTED_PRIVATE_KEY]",
    "jwt": "[REDACTED_JWT]",
    "connection_string": "[REDACTED_CONNECTION_STRING]",
    "generic_api_key_assignment": "[REDACTED_SECRET]",
    "secret_filename": "[REDACTED_SECRET_FILE]",
    "bearer_token": "[REDACTED_BEARER_TOKEN]",
}


def _spans_from_secrets(findings: Sequence[SecretFinding]) -> List[Tuple[int, int, str]]:
    return [
        (f.start, f.end, _SECRET_TOKEN.get(f.secret_type.value, "[REDACTED_SECRET]"))
        for f in findings
    ]


def _spans_from_pii(findings: Sequence) -> List[Tuple[int, int, str]]:
    spans = []
    for f in findings:
        key = f.pii_type.value if hasattr(f.pii_type, "value") else str(f.pii_type)
        spans.append((f.start, f.end, _PII_TOKEN.get(key, "[REDACTED_PII]")))
    return spans


def _apply_spans(text: str, spans: Sequence[Tuple[int, int, str]]) -> str:
    if not spans:
        return text
    ordered = sorted(spans, key=lambda s: (s[0], -(s[1] - s[0])))
    kept: List[Tuple[int, int, str]] = []
    last_end = -1
    for start, end, token in ordered:
        if start < last_end:
            continue
        kept.append((start, end, token))
        last_end = end
    out = text
    for start, end, token in sorted(kept, key=lambda s: s[0], reverse=True):
        out = out[:start] + token + out[end:]
    return out


def clean_text(
    text: str | None,
    *,
    redact_secrets: bool = True,
    redact_pii: bool = True,
) -> tuple[str, List[SecretFinding], List]:
    """Return ``(cleaned_text, secret_findings, pii_findings)``."""
    if text is None:
        return "", [], []
    if not isinstance(text, str):
        text = str(text)

    secret_findings = scan_secrets(text) if redact_secrets else []
    pii_findings = scan_pii(text) if redact_pii else []

    spans: List[Tuple[int, int, str]] = []
    if redact_secrets:
        spans.extend(_spans_from_secrets(secret_findings))
    if redact_pii:
        spans.extend(_spans_from_pii(pii_findings))

    cleaned = _apply_spans(text, spans)
    cleaned = re.sub(r"[^\S\n]{2,}", " ", cleaned)
    return cleaned, secret_findings, pii_findings
