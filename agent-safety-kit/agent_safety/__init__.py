"""
agent-safety-kit
================

Framework-agnostic safety for LLM agents:

* ``input_guard``  — block secrets / clean PII *before* the model call
* ``pii_scanner``  — scan free-text records *after* persistence (offline / batch)

Works with plain strings, OpenAI-style ``{"role","content"}`` message lists,
and optionally LangChain messages.
"""

from agent_safety.input_guard import GuardResult, guard_messages, guard_text, is_enabled
from agent_safety.pii_scanner import scan_text as scan_pii, scan_rows, scan_records

__all__ = [
    "GuardResult",
    "guard_text",
    "guard_messages",
    "is_enabled",
    "scan_pii",
    "scan_rows",
    "scan_records",
]

__version__ = "1.0.0"
