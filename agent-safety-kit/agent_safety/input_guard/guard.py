"""
Public API for the pre-LLM input guard.

``guard_text``     — inspect / clean a single string
``guard_messages`` — clean chat messages before ``llm.invoke``

Message formats supported (no hard dependency on LangChain):

* OpenAI-style dicts: ``{"role": "user", "content": "..."}``
* Any object with ``.content`` and optional ``.role`` / ``.type``
* LangChain ``HumanMessage`` / ``AIMessage`` / ``ToolMessage`` / ``SystemMessage``
  when ``langchain-core`` is installed
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, List, Mapping, MutableMapping, Optional, Sequence

from agent_safety.input_guard.cleaner import clean_text
from agent_safety.input_guard.policy import (
    block_pii,
    block_severity,
    is_enabled,
    pii_block_severity,
    redact_pii as pii_redact_enabled,
)
from agent_safety.input_guard.secrets import SecretFinding

logger = logging.getLogger(__name__)


@dataclass
class GuardResult:
    """Outcome of guarding one string or a message list."""

    original: str
    cleaned: str
    blocked: bool = False
    block_reason: Optional[str] = None
    secret_findings: List[SecretFinding] = field(default_factory=list)
    pii_findings: List[Any] = field(default_factory=list)
    redacted: bool = False

    @property
    def modified(self) -> bool:
        return self.cleaned != self.original

    def summary(self) -> dict:
        return {
            "blocked": self.blocked,
            "block_reason": self.block_reason,
            "redacted": self.redacted,
            "secret_types": [f.secret_type.value for f in self.secret_findings],
            "pii_types": [
                f.pii_type.value if hasattr(f, "pii_type") else str(f)
                for f in self.pii_findings
            ],
            "secret_count": len(self.secret_findings),
            "pii_count": len(self.pii_findings),
        }


def _decide_block(
    secret_findings: Sequence[SecretFinding],
    pii_findings: Sequence[Any],
) -> Optional[str]:
    threshold = block_severity()
    blockers = [f for f in secret_findings if f.severity >= threshold]
    if blockers:
        types = sorted({f.secret_type.value for f in blockers})
        return (
            "Blocked: potential secret(s) detected in input "
            f"({', '.join(types)}). Remove credentials / key files and retry. "
            "Never paste API keys, private keys, tokens, or credential files "
            "into the agent."
        )

    if block_pii():
        pii_thresh = pii_block_severity()
        pii_blockers = [
            f for f in pii_findings if getattr(f, "severity", 0) >= pii_thresh
        ]
        if pii_blockers:
            types = sorted(
                {
                    f.pii_type.value if hasattr(f.pii_type, "value") else str(f.pii_type)
                    for f in pii_blockers
                }
            )
            return (
                "Blocked: high-severity PII detected in input "
                f"({', '.join(types)}). Remove sensitive personal data and retry."
            )
    return None


def guard_text(
    text: str | None,
    *,
    allow_redaction: bool = True,
) -> GuardResult:
    """
    Guard a single text value before it reaches the LLM.

    Secrets at/above ``INPUT_GUARD_BLOCK_SEVERITY`` → ``blocked=True``.
    Otherwise secrets + PII are redacted when ``allow_redaction`` is True.
    """
    original = text if isinstance(text, str) else ("" if text is None else str(text))

    if not is_enabled():
        return GuardResult(original=original, cleaned=original)

    do_pii = pii_redact_enabled() and allow_redaction
    cleaned, secrets, pii = clean_text(
        original, redact_secrets=True, redact_pii=do_pii
    )

    reason = _decide_block(secrets, pii)
    if reason:
        logger.warning(
            "input_guard BLOCKED: secrets=%s pii=%s",
            [f.secret_type.value for f in secrets],
            [f.pii_type.value if hasattr(f, "pii_type") else str(f) for f in pii],
        )
        return GuardResult(
            original=original,
            cleaned=cleaned,
            blocked=True,
            block_reason=reason,
            secret_findings=list(secrets),
            pii_findings=list(pii),
            redacted=cleaned != original,
        )

    if cleaned != original:
        logger.info(
            "input_guard REDACTED: secrets=%d pii=%d",
            len(secrets),
            len(pii),
        )

    return GuardResult(
        original=original,
        cleaned=cleaned,
        blocked=False,
        secret_findings=list(secrets),
        pii_findings=list(pii),
        redacted=cleaned != original,
    )


def _msg_role(msg: Any) -> str:
    if isinstance(msg, Mapping):
        return str(msg.get("role") or msg.get("type") or "").lower()
    role = getattr(msg, "role", None) or getattr(msg, "type", None)
    if role:
        return str(role).lower()
    # LangChain class names
    name = type(msg).__name__.lower()
    if "system" in name:
        return "system"
    if "human" in name or "user" in name:
        return "user"
    if "ai" in name or "assistant" in name:
        return "assistant"
    if "tool" in name:
        return "tool"
    return ""


def _msg_content(msg: Any) -> Optional[str]:
    if isinstance(msg, Mapping):
        c = msg.get("content")
        return c if isinstance(c, str) else (str(c) if c is not None else None)
    c = getattr(msg, "content", None)
    return c if isinstance(c, str) else (str(c) if c is not None else None)


def _with_content(msg: Any, content: str) -> Any:
    """Return a copy of msg with updated content (dict or LangChain / duck-typed)."""
    if isinstance(msg, MutableMapping):
        new = dict(msg)
        new["content"] = content
        return new
    if isinstance(msg, Mapping):
        return {**dict(msg), "content": content}

    # LangChain-style reconstruction when available
    try:
        from langchain_core.messages import (
            AIMessage,
            HumanMessage,
            SystemMessage,
            ToolMessage,
        )

        if isinstance(msg, HumanMessage):
            return HumanMessage(content=content)
        if isinstance(msg, SystemMessage):
            return SystemMessage(content=content)
        if isinstance(msg, AIMessage):
            kwargs: dict = {}
            tcs = getattr(msg, "tool_calls", None)
            if tcs:
                kwargs["tool_calls"] = tcs
            addl = getattr(msg, "additional_kwargs", None)
            if addl:
                kwargs["additional_kwargs"] = addl
            return AIMessage(content=content, **kwargs)
        if isinstance(msg, ToolMessage):
            return ToolMessage(
                content=content,
                tool_call_id=getattr(msg, "tool_call_id", "") or "",
                name=getattr(msg, "name", None),
            )
    except ImportError:
        pass

    try:
        return msg.model_copy(update={"content": content})  # type: ignore[attr-defined]
    except Exception:
        return msg


def guard_messages(messages: List[Any]) -> tuple[List[Any], GuardResult]:
    """
    Clean a chat message list before calling the LLM.

    * ``system`` messages are left untouched (prompt templates)
    * ``user`` / ``assistant`` / ``tool`` (and Human/AI/Tool) content is cleaned
    * If any content hard-blocks, ``blocked=True`` — do not invoke the LLM

    Returns ``(messages, aggregate GuardResult)``.
    """
    if not is_enabled() or not messages:
        return messages, GuardResult(original="", cleaned="")

    out: List[Any] = []
    all_secrets: List[SecretFinding] = []
    all_pii: List[Any] = []
    any_blocked = False
    block_reason: Optional[str] = None
    any_redacted = False

    for msg in messages:
        role = _msg_role(msg)
        if role in {"system", "developer"}:
            out.append(msg)
            continue

        content = _msg_content(msg)
        if not content:
            out.append(msg)
            continue

        result = guard_text(content)
        all_secrets.extend(result.secret_findings)
        all_pii.extend(result.pii_findings)
        if result.blocked:
            any_blocked = True
            block_reason = block_reason or result.block_reason
        if result.redacted:
            any_redacted = True

        if result.cleaned == content:
            out.append(msg)
        else:
            out.append(_with_content(msg, result.cleaned))

    aggregate = GuardResult(
        original="[messages]",
        cleaned="[messages]",
        blocked=any_blocked,
        block_reason=block_reason,
        secret_findings=all_secrets,
        pii_findings=all_pii,
        redacted=any_redacted,
    )
    return out, aggregate


__all__ = ["GuardResult", "guard_text", "guard_messages", "is_enabled"]
