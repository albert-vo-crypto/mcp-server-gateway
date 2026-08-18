"""
Regex-based PII detectors for free-text fields.

Designed to be pure stdlib (no external NLP deps) so it can run in CI and
batch jobs without pulling heavyweight models. Patterns are intentionally
conservative: fewer false positives over maximal recall.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable, List, Optional, Sequence


class PiiType(str, Enum):
    EMAIL = "email"
    SSN = "ssn"
    PHONE = "phone"
    CREDIT_CARD = "credit_card"
    IPV4 = "ipv4"
    US_PASSPORT = "us_passport"
    DOB = "date_of_birth"


# Severity used for alerting thresholds (higher = more sensitive).
PII_SEVERITY: dict[PiiType, int] = {
    PiiType.SSN: 100,
    PiiType.CREDIT_CARD: 95,
    PiiType.US_PASSPORT: 90,
    PiiType.DOB: 70,
    PiiType.EMAIL: 50,
    PiiType.PHONE: 50,
    PiiType.IPV4: 30,
}


@dataclass(frozen=True)
class PiiFinding:
    """One PII match inside a text field."""

    pii_type: PiiType
    matched: str  # redacted form preferred for logs
    start: int
    end: int
    severity: int
    column: Optional[str] = None
    request_id: Optional[str] = None
    table: Optional[str] = None
    row_key: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "pii_type": self.pii_type.value,
            "matched": self.matched,
            "start": self.start,
            "end": self.end,
            "severity": self.severity,
            "column": self.column,
            "request_id": self.request_id,
            "table": self.table,
            "row_key": self.row_key,
        }


def redact(value: str, keep: int = 2) -> str:
    """Redact a match for safe logging: keep first/last ``keep`` chars."""
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return value[:keep] + ("*" * (len(value) - keep * 2)) + value[-keep:]


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

_EMAIL = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
)

# SSN: 123-45-6789 or 123456789 (reject obviously invalid area numbers)
_SSN = re.compile(
    r"\b(?!000|666|9\d{2})\d{3}[- ]?(?!00)\d{2}[- ]?(?!0000)\d{4}\b"
)

# US phone: +1 (415) 555-2671, 415-555-2671, 4155552671
_PHONE = re.compile(
    r"(?<!\d)(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}(?!\d)"
)

# Credit card: 13–19 digits with optional separators; Luhn-validated later
_CREDIT_CARD = re.compile(
    r"(?<!\d)(?:\d[ -]*?){13,19}(?!\d)"
)

_IPV4 = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
)

# US passport: 1 letter + 8 digits or 2 letters + 7 digits (common formats)
_US_PASSPORT = re.compile(r"\b[A-Z]{1,2}\d{7,8}\b")

# DOB phrases: DOB: 01/15/1985, born 1985-01-15, date of birth 1-15-85
_DOB = re.compile(
    r"(?i)\b(?:dob|date\s*of\s*birth|born(?:\s*on)?)\s*[:\-]?\s*"
    r"(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}|\d{4}[/\-.]\d{1,2}[/\-.]\d{1,2})"
)


def _luhn_ok(digits: str) -> bool:
    """Return True if ``digits`` passes the Luhn checksum."""
    if not digits.isdigit() or not (13 <= len(digits) <= 19):
        return False
    total = 0
    reverse = digits[::-1]
    for i, ch in enumerate(reverse):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _normalize_digits(raw: str) -> str:
    return re.sub(r"[^\d]", "", raw)


@dataclass
class Detector:
    """One named detector over a compiled regex (+ optional validator)."""

    pii_type: PiiType
    pattern: re.Pattern[str]
    validator: Optional[Callable[[str], bool]] = None
    # If set, use group(1) as the matched value (for patterns with surrounding words)
    capture_group: Optional[int] = None

    def find(self, text: str) -> List[PiiFinding]:
        out: List[PiiFinding] = []
        for m in self.pattern.finditer(text):
            raw = m.group(self.capture_group) if self.capture_group else m.group(0)
            if self.validator and not self.validator(raw):
                continue
            start = m.start(self.capture_group) if self.capture_group else m.start()
            end = m.end(self.capture_group) if self.capture_group else m.end()
            out.append(
                PiiFinding(
                    pii_type=self.pii_type,
                    matched=redact(raw),
                    start=start,
                    end=end,
                    severity=PII_SEVERITY[self.pii_type],
                )
            )
        return out


def _cc_validator(raw: str) -> bool:
    return _luhn_ok(_normalize_digits(raw))


def _ssn_validator(raw: str) -> bool:
    digits = _normalize_digits(raw)
    if len(digits) != 9:
        return False
    area, group, serial = digits[:3], digits[3:5], digits[5:]
    if area in {"000", "666"} or area.startswith("9"):
        return False
    if group == "00" or serial == "0000":
        return False
    return True


def _phone_validator(raw: str) -> bool:
    digits = _normalize_digits(raw)
    # 10 digits, or 11 with leading 1
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return False
    # Reject all-same-digit and obvious placeholders
    if len(set(digits)) == 1:
        return False
    if digits[:3] in {"000", "111", "555"} and digits[3:6] == "555":
        # Allow 555-01xx (fiction) to still flag — better to alert
        pass
    return True


DEFAULT_DETECTORS: Sequence[Detector] = (
    Detector(PiiType.EMAIL, _EMAIL),
    Detector(PiiType.SSN, _SSN, validator=_ssn_validator),
    Detector(PiiType.PHONE, _PHONE, validator=_phone_validator),
    Detector(PiiType.CREDIT_CARD, _CREDIT_CARD, validator=_cc_validator),
    Detector(PiiType.IPV4, _IPV4),
    Detector(PiiType.US_PASSPORT, _US_PASSPORT),
    Detector(PiiType.DOB, _DOB, capture_group=1),
)


def scan_text(
    text: str | None,
    *,
    detectors: Iterable[Detector] = DEFAULT_DETECTORS,
    column: str | None = None,
    request_id: str | None = None,
    table: str | None = None,
    row_key: str | None = None,
) -> List[PiiFinding]:
    """Scan a single text value and return all PII findings."""
    if not text or not isinstance(text, str) or not text.strip():
        return []
    findings: List[PiiFinding] = []
    for det in detectors:
        for f in det.find(text):
            findings.append(
                PiiFinding(
                    pii_type=f.pii_type,
                    matched=f.matched,
                    start=f.start,
                    end=f.end,
                    severity=f.severity,
                    column=column,
                    request_id=request_id,
                    table=table,
                    row_key=row_key,
                )
            )
    # Deduplicate overlapping identical matches
    seen: set[tuple] = set()
    unique: List[PiiFinding] = []
    for f in findings:
        key = (f.pii_type, f.start, f.end, f.matched)
        if key in seen:
            continue
        seen.add(key)
        unique.append(f)
    return unique
