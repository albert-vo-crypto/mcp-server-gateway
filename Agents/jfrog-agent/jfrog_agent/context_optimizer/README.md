# `agent.context_optimizer`

A framework-agnostic, layered context-window optimizer for LLM agents.

> **Why it exists.** As an agent's conversation grows, every LLM call re-sends
> the full chat history. Token cost grows quadratically, latency grows, and
> eventually you hit the context window. This module gives you a single,
> swappable component that trims the history before each call while
> preserving the invariants every chat agent depends on (system prompts,
> assistant `tool_call` ↔ `tool` response pairs, the current user turn).

---

## The funnel

```
raw messages
    │
    ▼
[ Compression ]     squeeze content inside individual items
[ Reranking   ]     score each item by relevance to the current task
[ Selection   ]     pick which items to keep within the token budget
[ Clustering  ]     group / dedupe near-identical items
    │
    ▼
optimized messages
```

Each layer does **one** thing and is independently swappable. Pipelines
choose which layers to run and in what order. The standard funnel is
above, but plenty of pipelines skip layers (e.g. `MINIMAL` is just
Compression → Selection).

---

## Three ways to use it

### 1. Drop-in preset (recommended)

```python
from agent.context_optimizer import presets, adapters

items = adapters.langchain_adapter.to_items(state["messages"])
result = presets.balanced(model="gpt-4o-mini", max_tokens=8_000).run(
    items, task=state["user_query"],
)
state["messages"] = adapters.langchain_adapter.from_items(result.items)
print(result.summary())   # per-layer breakdown of tokens saved
```

### 2. Custom pipeline

```python
from agent.context_optimizer import Pipeline
from agent.context_optimizer.layers import (
    ToolResultCompression, MessageDedup, BM25Ranker,
    TokenBudgetSelector, RepeatedToolCallMerger,
)

my_pipeline = Pipeline(
    [
        MessageDedup(),
        RepeatedToolCallMerger(),
        ToolResultCompression(max_chars=1500),
        BM25Ranker(),
        TokenBudgetSelector(),
    ],
    model="gpt-4o-mini",
    max_tokens=6_000,
    name="my_team_pipeline",
)
```

### 3. Backward-compat (this BigQuery agent)

```python
from agent.context_optimizer import get_optimizer

opt = get_optimizer()            # honours CTX_OPT_* env vars; None if disabled
if opt is not None:
    messages, meta = opt.optimize(messages, task=user_query)
```

---

## Adding a new agent framework

Drop one file in `adapters/` exposing `to_items` and `from_items`. Every
layer keeps working. See `langchain_adapter.py` (~80 lines),
`openai_adapter.py`, and `generic_adapter.py` (works for Anthropic,
Bedrock, vLLM, raw transformers, any `{"role","content"}` shape) as
reference implementations.

```python
# adapters/my_framework_adapter.py
from ..core.item import ContextItem

name = "my_framework"

def to_items(messages) -> list[ContextItem]: ...
def from_items(items: list[ContextItem]): ...
```

---

## Adding a new layer

Subclass `Layer` and implement `apply`. Layers operate on `list[ContextItem]`
and return the same. The pipeline will repair any broken invariants (pinned
items, tool pairs) so layers don't have to worry about them — they only need
to be correct on the happy path.

```python
from agent.context_optimizer import Layer, ContextItem, OptimizationContext

class MyLayer(Layer):
    name = "selection:my_idea"

    def apply(
        self, items: list[ContextItem], ctx: OptimizationContext
    ) -> list[ContextItem]:
        # ctx.task, ctx.model, ctx.max_tokens are available
        ...
```

---

## What ships out of the box

| Stage | Class | Notes |
|---|---|---|
| Compression | `ToolResultCompression` | Trim oversized `role="tool"` outputs (BigQuery tables, etc.) via head/tail row preservation. |
| Compression | `MessageDedup` | Drop exact-content duplicates (defensive; some orchestrators re-append the system prompt every turn). |
| Compression | `PromptSummarizer` | LLM-backed summary of older history. Optional. |
| Reranking | `BM25Ranker` | Pure-stdlib Okapi BM25 against `ctx.task`. No embeddings needed. |
| Reranking | `RecencyRanker` | Exponential recency decay. |
| Reranking | `CompositeRanker` | Weighted combination of any rerankers. |
| Selection | `TokenBudgetSelector` | Keeps highest-score items until budget is hit. |
| Selection | `SlidingWindowSelector` | Keeps the last N non-pinned items. |
| Selection | `TopKSelector` | Keeps top K by score. |
| Clustering | `NearDuplicateClusterer` | Jaccard(3-shingles) ≥ threshold → keep newest per cluster. |
| Clustering | `RepeatedToolCallMerger` | Collapses `N` identical tool-call + response pairs into one with `[repeated N times]`. |

Presets in `presets.py`:

| Preset | Layers | Use when |
|---|---|---|
| `minimal()` | Compression + dedup + sliding-window | Short conversations, no LLM cost overhead allowed. |
| `balanced()` | Above + RepeatedToolCallMerger + BM25/Recency + TokenBudget | Default for production agents. No LLM call inside the optimizer. |
| `aggressive(summarizer=...)` | Full funnel + NearDuplicateClusterer + optional summarizer | Long-running agents where context cost dominates. |

---

## Env vars (backward-compat path)

| Var | Default | Notes |
|---|---|---|
| `CTX_OPT_ENABLED` | unset | Truthy to enable. When unset, `get_optimizer()` returns `None`. |
| `CTX_OPT_PRESET` | `balanced` | `minimal` / `balanced` / `aggressive`. |
| `CTX_OPT_MODEL` | `gpt-4o-mini` | Used for token counting + pricing. |
| `CTX_OPT_MAX_TOKENS` | `8000` | Target budget. |
| `CTX_OPT_USE_SUMMARIZER` | unset | Only used by `aggressive`. Truthy to add LLM-based summarization. |
| `CTX_OPT_STRATEGY` | unset | Legacy alias: `sliding-window`→`minimal`, `relevance`/`summarizer`→`balanced`, `hybrid`→`aggressive`. |

---

## Invariants enforced by `Pipeline`

Even if a layer is buggy, the pipeline will repair these before the next
layer runs:

1. **Pinned items survive.** Pinning is set by adapters (every `SystemMessage`
   and the most-recent user message are pinned). Layers can also pin items
   they produce (e.g. `PromptSummarizer` pins the summary it generates).
2. **`tool_call` ↔ `tool` pairs stay together.** OpenAI rejects requests
   where a `tool` message has no preceding assistant `tool_call`. Drop
   both ends or keep both.

If you write a layer that breaks one of these, the pipeline will silently
fix the output. You'll see it in the per-layer `LayerResult` metrics (the
item count won't match what your layer thought it returned).
