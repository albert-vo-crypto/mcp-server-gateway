"""Tests for pre-LLM input_guard."""

from __future__ import annotations

from agent_safety.input_guard import guard_messages, guard_text, is_enabled
from agent_safety.input_guard.cleaner import clean_text
from agent_safety.input_guard.secrets import SecretType, scan_secrets


def test_detect_openai_key():
    findings = scan_secrets("my key is sk-proj-abcdefghijklmnopqrstuvwxyz012345")
    assert any(f.secret_type == SecretType.OPENAI_KEY for f in findings)


def test_detect_aws_access_key():
    assert any(
        f.secret_type == SecretType.AWS_ACCESS_KEY
        for f in scan_secrets("AKIAIOSFODNN7EXAMPLE")
    )


def test_detect_github_token():
    assert any(
        f.secret_type == SecretType.GITHUB_TOKEN
        for f in scan_secrets("token ghp_abcdefghijklmnopqrstuvwxyz0123456789")
    )


def test_detect_private_key_block():
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEowIBAAKCAQEA0Z3VS5JJcds3xfn/ygWyF6PZGBremzEXAMPLEKEYDATAHERE\n"
        "-----END RSA PRIVATE KEY-----"
    )
    assert any(f.secret_type == SecretType.PRIVATE_KEY_BLOCK for f in scan_secrets(pem))


def test_detect_secret_filename():
    assert any(
        f.secret_type == SecretType.SECRET_FILENAME
        for f in scan_secrets("please use my credentials.json and id_rsa")
    )


def test_clean_text_redacts_without_raw_leak():
    raw = "email jane.doe@example.com and key sk-abcdefghijklmnopqrstuv"
    cleaned, secrets, pii = clean_text(raw)
    assert "jane.doe@example.com" not in cleaned
    assert "sk-abcdefghijklmnopqrstuv" not in cleaned
    assert "[REDACTED_EMAIL]" in cleaned
    assert "[REDACTED_OPENAI_KEY]" in cleaned
    assert secrets and pii


def test_guard_blocks_high_severity_secret(monkeypatch):
    monkeypatch.setenv("INPUT_GUARD_ENABLED", "1")
    monkeypatch.setenv("INPUT_GUARD_BLOCK_SEVERITY", "90")
    result = guard_text("here is sk-abcdefghijklmnopqrstuvwxyz0123")
    assert result.blocked is True
    assert result.block_reason


def test_guard_redacts_pii_does_not_block_by_default(monkeypatch):
    monkeypatch.setenv("INPUT_GUARD_ENABLED", "1")
    monkeypatch.setenv("INPUT_GUARD_BLOCK_PII", "0")
    monkeypatch.setenv("INPUT_GUARD_REDACT_PII", "1")
    result = guard_text("contact me at alice@example.com about datasets")
    assert result.blocked is False
    assert result.redacted is True
    assert "[REDACTED_EMAIL]" in result.cleaned


def test_guard_can_block_high_severity_pii(monkeypatch):
    monkeypatch.setenv("INPUT_GUARD_ENABLED", "1")
    monkeypatch.setenv("INPUT_GUARD_BLOCK_PII", "1")
    monkeypatch.setenv("INPUT_GUARD_PII_BLOCK_SEVERITY", "95")
    result = guard_text("member SSN is 219-09-9999")
    assert result.blocked is True


def test_guard_disabled_passthrough(monkeypatch):
    monkeypatch.setenv("INPUT_GUARD_ENABLED", "0")
    raw = "sk-abcdefghijklmnopqrstuvwxyz0123"
    result = guard_text(raw)
    assert result.blocked is False
    assert result.cleaned == raw


def test_is_enabled_default_on(monkeypatch):
    monkeypatch.delenv("INPUT_GUARD_ENABLED", raising=False)
    assert is_enabled() is True


def test_guard_messages_openai_style_dicts(monkeypatch):
    monkeypatch.setenv("INPUT_GUARD_ENABLED", "1")
    monkeypatch.setenv("INPUT_GUARD_BLOCK_SEVERITY", "90")
    msgs = [
        {"role": "system", "content": "You are a helpful assistant"},
        {"role": "user", "content": "email bob@example.com about tables"},
        {"role": "tool", "content": "rows for bob@example.com", "tool_call_id": "t1"},
    ]
    cleaned, result = guard_messages(msgs)
    assert result.blocked is False
    assert cleaned[0]["content"] == "You are a helpful assistant"
    assert "[REDACTED_EMAIL]" in cleaned[1]["content"]
    assert "[REDACTED_EMAIL]" in cleaned[2]["content"]


def test_guard_messages_blocks_secret_in_tool_output(monkeypatch):
    monkeypatch.setenv("INPUT_GUARD_ENABLED", "1")
    monkeypatch.setenv("INPUT_GUARD_BLOCK_SEVERITY", "90")
    msgs = [
        {"role": "user", "content": "run query"},
        {
            "role": "tool",
            "content": "secret AKIAIOSFODNN7EXAMPLE leaked from table",
            "tool_call_id": "1",
        },
    ]
    _, result = guard_messages(msgs)
    assert result.blocked is True
