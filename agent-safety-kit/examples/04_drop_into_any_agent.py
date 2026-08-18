#!/usr/bin/env python3
"""
Example 4 — drop-in hook pattern for ANY agent framework.

Pseudocode for LangGraph / CrewAI / custom loops:

    messages = ...
    messages, guard = guard_messages(messages)
    if guard.blocked:
        return {"error": guard.block_reason}
    response = llm.invoke(messages)   # or client.chat.completions.create(...)
"""

from agent_safety import guard_messages, guard_text


def before_llm(user_text: str, history: list[dict]) -> tuple[list[dict] | None, str | None]:
    """Return (safe_messages, error). error set ⇒ do not call the model."""
    g = guard_text(user_text)
    if g.blocked:
        return None, g.block_reason

    messages = history + [{"role": "user", "content": g.cleaned}]
    safe, result = guard_messages(messages)
    if result.blocked:
        return None, result.block_reason
    return safe, None


def fake_llm(messages: list[dict]) -> str:
    return f"OK — received {len(messages)} messages (demo)."


if __name__ == "__main__":
    history = [{"role": "system", "content": "You are helpful."}]

    for prompt in [
        "What tables exist?",
        "email bob@example.com the summary",
        "use AKIAIOSFODNN7EXAMPLE for access",
    ]:
        safe, err = before_llm(prompt, history)
        print("=" * 50)
        print("prompt:", prompt)
        if err:
            print("BLOCKED:", err)
        else:
            print("LLM:", fake_llm(safe or []))
