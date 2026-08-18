"""
Policy knobs for the pre-LLM input guard.

Env vars:
  INPUT_GUARD_ENABLED          default: on (1/true/yes). Set 0 to disable.
  INPUT_GUARD_BLOCK_SEVERITY   default: 90 — secrets at/above this → hard block
  INPUT_GUARD_REDACT_PII       default: on — redact PII instead of blocking
  INPUT_GUARD_BLOCK_PII        default: off — if on, PII at high severity also blocks
  INPUT_GUARD_PII_BLOCK_SEVERITY default: 95 — only SSN/CC block when BLOCK_PII on
"""

from __future__ import annotations

import os


def _truthy(name: str, default: str = "") -> bool:
    v = os.environ.get(name, default).strip().lower()
    if not v:
        return default.strip().lower() in {"1", "true", "yes", "on"}
    return v in {"1", "true", "yes", "on"}


def is_enabled() -> bool:
    # Default ON — safety first. Explicitly disable with INPUT_GUARD_ENABLED=0
    raw = os.environ.get("INPUT_GUARD_ENABLED", "1").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def block_severity() -> int:
    try:
        return int(os.environ.get("INPUT_GUARD_BLOCK_SEVERITY", "90").strip())
    except ValueError:
        return 90


def redact_pii() -> bool:
    return _truthy("INPUT_GUARD_REDACT_PII", "1")


def block_pii() -> bool:
    return _truthy("INPUT_GUARD_BLOCK_PII", "0")


def pii_block_severity() -> int:
    try:
        return int(os.environ.get("INPUT_GUARD_PII_BLOCK_SEVERITY", "95").strip())
    except ValueError:
        return 95
