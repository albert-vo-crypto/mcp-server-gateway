"""
Adapters convert between framework-specific message types and the canonical
``ContextItem``. Add a new adapter to support a new agent framework — every
layer and pipeline keeps working unchanged.

Provided out of the box:
  * ``langchain_adapter``  — for ``langchain_core.messages.BaseMessage`` lists
  * ``openai_adapter``     — for OpenAI Chat-Completions dicts (incl. tool calls)
  * ``generic_adapter``    — for plain ``{"role", "content"}`` dicts (Anthropic,
                              Bedrock, vLLM, raw transformers, custom agents)
"""

from . import generic_adapter, langchain_adapter, openai_adapter
from .base import Adapter

__all__ = [
    "Adapter",
    "langchain_adapter",
    "openai_adapter",
    "generic_adapter",
]
