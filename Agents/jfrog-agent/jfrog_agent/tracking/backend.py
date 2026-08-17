"""Tracking + eval backend interface (separate from conversation memory)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class TrackingBackend(ABC):
    """Records LLM usage, human feedback, and full run summaries for eval pipelines."""

    @abstractmethod
    def record_llm_call(self, **kwargs: Any) -> None: ...

    @abstractmethod
    def list_llm_calls(self, limit: int = 500) -> list[dict[str, Any]]: ...

    @abstractmethod
    def llm_summary(self) -> dict[str, Any]: ...

    @abstractmethod
    def record_feedback(self, run_id: str | None, thread_id: str | None, rating: int, note: str = "") -> None: ...

    @abstractmethod
    def feedback_summary(self) -> dict[str, Any]: ...

    @abstractmethod
    def record_run(self, **kwargs: Any) -> None: ...

    def close(self) -> None:  # pragma: no cover
        pass
