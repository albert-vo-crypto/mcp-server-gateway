#!/usr/bin/env python3
"""Example 2 — clean OpenAI-style messages before chat.completions.create."""

from agent_safety import guard_messages

messages = [
    {"role": "system", "content": "You are a data analyst assistant."},
    {
        "role": "user",
        "content": "Summarize this for alice@example.com using key sk-abc123notarealkey000111222",
    },
    {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "Query returned phone (415) 555-2671 for the member",
    },
]

cleaned, result = guard_messages(messages)

print("summary:", result.summary())
print()
for m in cleaned:
    print(f"[{m['role']}] {m['content'][:120]}")

if result.blocked:
    print("\n*** DO NOT call the LLM — secrets detected ***")
else:
    print("\nSafe to call the LLM with `cleaned` messages.")
