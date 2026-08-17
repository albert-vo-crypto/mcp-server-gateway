"""Tracking + eval backends (BigQuery production, SQLite local dev)."""

from __future__ import annotations

from ..settings import Settings, settings as default_settings
from .backend import TrackingBackend


class NoOpTrackingBackend(TrackingBackend):
    """Discards all tracking events."""

    def record_llm_call(self, **kwargs) -> None:
        pass

    def list_llm_calls(self, limit: int = 500) -> list:
        return []

    def llm_summary(self) -> dict:
        return {"calls": 0, "tokens": 0, "cost": 0.0, "avg_latency_ms": 0.0, "by_model": []}

    def record_feedback(self, run_id, thread_id, rating: int, note: str = "") -> None:
        pass

    def feedback_summary(self) -> dict:
        return {"total": 0, "up": 0, "down": 0}

    def record_run(self, **kwargs) -> None:
        pass


def get_tracking_backend(settings: Settings = default_settings) -> TrackingBackend:
    backend = (settings.tracking_backend or "sqlite").lower()
    if backend == "none" or backend == "noop":
        return NoOpTrackingBackend()
    if backend == "bigquery":
        from .bq_backend import BigQueryTrackingBackend

        return BigQueryTrackingBackend(settings)
    from .sqlite_backend import SqliteTrackingBackend

    return SqliteTrackingBackend(settings)


__all__ = ["TrackingBackend", "NoOpTrackingBackend", "get_tracking_backend"]
