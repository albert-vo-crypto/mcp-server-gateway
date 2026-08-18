# Agent Safety Kit

**Framework-agnostic** safety for LLM agents:

1. **Input Guard** — block secrets / credential files and clean PII *before* the model call  
2. **PII Scanner** — scan free-text telemetry *after* persistence (offline / batch)

Designed so journal and book readers can **reproduce the full file tree**, run tests, and drop the hooks into LangGraph, OpenAI chat loops, CrewAI, or any custom agent — with **no cloud account required**.

---

## Why this exists

Agents accept free text, call tools, and often store prompts / tool outputs for evaluation. That creates three failure modes:

| Risk | What goes wrong |
|---|---|
| Secret paste | Users paste API keys, PEM blocks, `credentials.json` into chat |
| Tool echo | Tool results re-inject emails / tokens into the next LLM turn |
| Warehouse residue | Tracking tables keep raw text for weeks without anyone looking |

**Post-hoc scanning alone is too late** for secrets (they already hit the model). This kit implements **prevent + detect**.

```
User / tool text
      │
      ▼
┌─────────────────────┐
│  INPUT GUARD        │  secrets ≥ threshold → HARD BLOCK
│  (pre-LLM)          │  PII → [REDACTED_*] tokens
└──────────┬──────────┘
           │ only if not blocked
           ▼
      LLM invoke
           │
           ▼
   Persist telemetry
           │
           ▼
┌─────────────────────┐
│  PII SCANNER        │  batch / offline scan of free-text fields
│  (post-write)       │  redacted alerts (log / webhook)
└─────────────────────┘
```

---

## File structure

```text
agent-safety-kit/
├── agent_safety/
│   ├── input_guard/     # pre-LLM block + redact
│   └── pii_scanner/     # post-write detect + alert
├── examples/            # 01–04 runnable demos
└── tests/               # pytest
```

---

## Install & reproduce (5 minutes)

```bash
cd agent-safety-kit
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

pytest -v

python examples/01_guard_text.py
python examples/02_guard_messages_openai_style.py
python examples/03_pii_scan_offline.py
python examples/04_drop_into_any_agent.py
python -m agent_safety.pii_scanner.entrypoint --demo
```

Runtime dependency: **Python 3.10+ stdlib only**. Optional: `langchain-core` if you pass LangChain message objects.

---

## What gets blocked / redacted

### Input Guard — secrets (default block severity ≥ 90)

Private keys, AWS / GCP keys, GitHub / OpenAI / Slack / Stripe tokens, JWTs, Bearer tokens, DB connection strings with passwords, secret filenames (`credentials.json`, `*.pem`, `.env`, `id_rsa`), and `api_key=` / `password=` assignments.

### Input Guard — PII (redact by default)

Email, phone, SSN, credit card (Luhn), IPv4, passport, DOB → replaced with typed tokens like `[REDACTED_EMAIL]`.

Optional hard-block for high-severity PII: `INPUT_GUARD_BLOCK_PII=1`.

### PII Scanner — tables (logical names)

`agent_job_tracking`, `agent_trajectory`, `agent_tool_invocations`, `agent_quality_eval`, `agent_eval_scores`, `agent_quality_metrics_detail` — edit `pii_scanner/registry.py` to match your schema.

---

## Drop into any agent

```python
from agent_safety import guard_text, guard_messages

g = guard_text(user_query)
if g.blocked:
    return g.block_reason          # do not call the LLM
user_query = g.cleaned

messages, result = guard_messages(messages)   # OpenAI dicts or LangChain
if result.blocked:
    return result.block_reason
response = llm.invoke(messages)
```

OpenAI-style dicts work with **zero** extra dependencies:

```python
msgs = [
    {"role": "system", "content": "You are helpful."},
    {"role": "user", "content": "email alice@example.com"},
]
clean, result = guard_messages(msgs)
```

---

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `INPUT_GUARD_ENABLED` | `1` | Master switch for pre-LLM guard |
| `INPUT_GUARD_BLOCK_SEVERITY` | `90` | Secrets ≥ this → hard block |
| `INPUT_GUARD_REDACT_PII` | `1` | Redact PII in context |
| `INPUT_GUARD_BLOCK_PII` | `0` | Also hard-block high-severity PII |
| `INPUT_GUARD_PII_BLOCK_SEVERITY` | `95` | PII block threshold when enabled |
| `PII_ALERT_MIN_SEVERITY` | `50` | Post-write alert threshold |
| `PII_ALERT_WEBHOOK_URL` | unset | Optional Slack/webhook URL |

---

## How this helps

- Secrets **never reach** the model (or provider prompt logs) when blocked  
- Tool outputs are cleaned on the way **back into** the loop  
- Users get a **clear error** instead of a silent leak  
- Offline scanner proves residual PII in eval stores is **detectable and attributable**  
- Entire kit is **reproducible** for education and audits  

---

## Roadmap (for the journal’s “what’s next” section)

1. **Productionize** — cron the offline scanner against your warehouse export; Slack channel; false-positive baseline  
2. **Smarter detection** — allowlists, entropy-based unknown secrets, optional NER / DLP hybrid  
3. **Shift further left** — scan file uploads; redact again at persistence time; UI warnings  
4. **Platform** — YAML policy per agent; auto-remediation after approval; compliance exports  

**North star:** high-severity secrets reaching `llm.invoke` → **zero**; PII findings per 1,000 runs trending down with &lt;24h remediation.

---

## License

MIT — free to adapt for books, courses, and production agents.
