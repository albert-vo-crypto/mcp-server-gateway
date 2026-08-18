"""
Secret and secret-file detectors for pre-LLM blocking / redaction.

Designed to catch credentials *before* they enter the model context:
  - Cloud / SaaS API keys (AWS, GCP, GitHub, Slack, OpenAI, Stripe, …)
  - PEM / private key blocks
  - JWTs
  - Connection strings with embedded passwords
  - Uploaded / pasted secret filenames (*.pem, credentials.json, …)
  - Inline "key=..." / "password=..." assignments
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, List, Optional, Sequence


class SecretType(str, Enum):
    AWS_ACCESS_KEY = "aws_access_key"
    AWS_SECRET_KEY = "aws_secret_key"
    GCP_API_KEY = "gcp_api_key"
    GITHUB_TOKEN = "github_token"
    SLACK_TOKEN = "slack_token"
    OPENAI_KEY = "openai_key"
    STRIPE_KEY = "stripe_key"
    PRIVATE_KEY_BLOCK = "private_key_block"
    JWT = "jwt"
    CONNECTION_STRING = "connection_string"
    GENERIC_API_KEY_ASSIGNMENT = "generic_api_key_assignment"
    SECRET_FILENAME = "secret_filename"
    BEARER_TOKEN = "bearer_token"


# Higher = more dangerous. Block threshold defaults to 90.
SECRET_SEVERITY: dict[SecretType, int] = {
    SecretType.PRIVATE_KEY_BLOCK: 100,
    SecretType.AWS_SECRET_KEY: 100,
    SecretType.AWS_ACCESS_KEY: 95,
    SecretType.GITHUB_TOKEN: 95,
    SecretType.OPENAI_KEY: 95,
    SecretType.STRIPE_KEY: 95,
    SecretType.SLACK_TOKEN: 95,
    SecretType.CONNECTION_STRING: 95,
    SecretType.BEARER_TOKEN: 90,
    SecretType.JWT: 90,
    SecretType.GCP_API_KEY: 90,
    SecretType.GENERIC_API_KEY_ASSIGNMENT: 85,
    SecretType.SECRET_FILENAME: 90,  # treat pasted credential filenames as block-worthy
}


@dataclass(frozen=True)
class SecretFinding:
    secret_type: SecretType
    matched: str  # redacted
    start: int
    end: int
    severity: int
    action: str = "block"  # block | redact

    def as_dict(self) -> dict:
        return {
            "secret_type": self.secret_type.value,
            "matched": self.matched,
            "start": self.start,
            "end": self.end,
            "severity": self.severity,
            "action": self.action,
        }


def _redact(value: str, keep: int = 3) -> str:
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return value[:keep] + ("*" * (len(value) - keep * 2)) + value[-keep:]


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

_AWS_ACCESS = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
# AWS secret access keys are 40 chars base64-ish; require nearby "secret"/"aws"
_AWS_SECRET = re.compile(
    r"(?i)(?:aws)?[_-]?(?:secret|secret[_-]?access[_-]?key)\s*[:=]\s*"
    r"['\"]?([A-Za-z0-9/+=]{40})['\"]?"
)
_GCP_API = re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b")
_GITHUB = re.compile(
    r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,255}\b"
    r"|\bgithub_pat_[A-Za-z0-9_]{20,255}\b"
)
_SLACK = re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b")
_OPENAI = re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}\b")
_STRIPE = re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}\b")
_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----[\s\S]+?"
    r"-----END (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----"
)
_JWT = re.compile(
    r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"
)
_CONN = re.compile(
    r"(?i)\b(?:postgres|postgresql|mysql|mongodb|redis|amqp|kafka)://"
    r"[^\s:]+:[^\s@]+@[^\s]+"
)
_BEARER = re.compile(
    r"(?i)\b(?:authorization\s*:\s*)?bearer\s+([A-Za-z0-9_\-\.=]{20,})"
)
_GENERIC_ASSIGN = re.compile(
    r"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|"
    r"private[_-]?key|secret[_-]?key|password|passwd|pwd)\s*[:=]\s*"
    r"['\"]?([^\s'\"]{8,})['\"]?"
)
# Filenames / paths that almost always mean a secret was pasted
_SECRET_FILE = re.compile(
    r"(?i)\b(?:[\w./-]+/)?"
    r"(?:"
    r"credentials\.json|service[_-]?account\.json|key\.json|"
    r"application_default_credentials\.json|"
    r"id_rsa|id_ed25519|id_ecdsa|"
    r"[\w.-]+\.(?:pem|p12|pfx|key|keystore|jks)|"
    r"\.env(?:\.\w+)?|"
    r"aws_credentials|gcloud[_-]?credentials"
    r")\b"
)

# Also detect PEM-looking content without full BEGIN/END (partial paste)
_PEM_BODY = re.compile(
    r"(?i)-----BEGIN[ A-Z]*PRIVATE KEY-----"
)


@dataclass
class SecretDetector:
    secret_type: SecretType
    pattern: re.Pattern[str]
    capture_group: Optional[int] = None
    default_action: str = "block"

    def find(self, text: str) -> List[SecretFinding]:
        out: List[SecretFinding] = []
        for m in self.pattern.finditer(text):
            raw = m.group(self.capture_group) if self.capture_group else m.group(0)
            start = m.start(self.capture_group) if self.capture_group else m.start()
            end = m.end(self.capture_group) if self.capture_group else m.end()
            out.append(
                SecretFinding(
                    secret_type=self.secret_type,
                    matched=_redact(raw),
                    start=start,
                    end=end,
                    severity=SECRET_SEVERITY[self.secret_type],
                    action=self.default_action,
                )
            )
        return out


DEFAULT_SECRET_DETECTORS: Sequence[SecretDetector] = (
    SecretDetector(SecretType.PRIVATE_KEY_BLOCK, _PRIVATE_KEY),
    SecretDetector(SecretType.PRIVATE_KEY_BLOCK, _PEM_BODY),
    SecretDetector(SecretType.AWS_ACCESS_KEY, _AWS_ACCESS),
    SecretDetector(SecretType.AWS_SECRET_KEY, _AWS_SECRET, capture_group=1),
    SecretDetector(SecretType.GCP_API_KEY, _GCP_API),
    SecretDetector(SecretType.GITHUB_TOKEN, _GITHUB),
    SecretDetector(SecretType.SLACK_TOKEN, _SLACK),
    SecretDetector(SecretType.OPENAI_KEY, _OPENAI),
    SecretDetector(SecretType.STRIPE_KEY, _STRIPE),
    SecretDetector(SecretType.JWT, _JWT),
    SecretDetector(SecretType.CONNECTION_STRING, _CONN),
    SecretDetector(SecretType.BEARER_TOKEN, _BEARER, capture_group=1),
    SecretDetector(
        SecretType.GENERIC_API_KEY_ASSIGNMENT, _GENERIC_ASSIGN, capture_group=1
    ),
    SecretDetector(
        SecretType.SECRET_FILENAME, _SECRET_FILE, default_action="block"
    ),
)


def scan_secrets(
    text: str | None,
    *,
    detectors: Iterable[SecretDetector] = DEFAULT_SECRET_DETECTORS,
) -> List[SecretFinding]:
    if not text or not isinstance(text, str) or not text.strip():
        return []
    findings: List[SecretFinding] = []
    seen: set[tuple] = set()
    for det in detectors:
        for f in det.find(text):
            key = (f.secret_type, f.start, f.end)
            if key in seen:
                continue
            seen.add(key)
            findings.append(f)
    return findings
