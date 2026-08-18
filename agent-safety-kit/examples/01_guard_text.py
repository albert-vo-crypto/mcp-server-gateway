#!/usr/bin/env python3
"""Example 1 — guard a single user string before calling any LLM."""

from agent_safety import guard_text

samples = [
    "List datasets in project analytics-prod",
    "email results to jane.doe@example.com",
    "debug with sk-abcdefghijklmnopqrstuvwxyz0123",
    "here is my credentials.json contents",
]

for text in samples:
    result = guard_text(text)
    print("-" * 60)
    print("IN :", text)
    print("OUT:", result.cleaned)
    print("blocked=", result.blocked, "redacted=", result.redacted)
    if result.block_reason:
        print("reason:", result.block_reason)
