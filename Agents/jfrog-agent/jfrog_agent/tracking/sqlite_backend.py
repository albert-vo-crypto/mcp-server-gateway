"""SQLite tracking backend — local dev fallback (same file as memory, separate tables)."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from ..settings import Settings, settings as default_settings
from .backend import TrackingBackend


class SqliteTrackingBackend(TrackingBackend):
    """Uses llm_calls / llm_feedback tables in the tracking DB (defaults to memory.db)."""

    def __init__(self, settings: Settings = default_settings):
        self.s = settings
        self.s.tracking_db.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.s.tracking_db))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS llm_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT, thread_id TEXT, purpose TEXT,
                    provider TEXT, model TEXT,
                    prompt_tokens INTEGER, completion_tokens INTEGER, total_tokens INTEGER,
                    latency_ms REAL, ok INTEGER, cost REAL, ts REAL
                );
                CREATE TABLE IF NOT EXISTS llm_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT, thread_id TEXT, rating INTEGER, note TEXT, ts REAL
                );
                CREATE INDEX IF NOT EXISTS ix_llm_calls_ts ON llm_calls(ts);
                """
            )

    def record_llm_call(self, **kw: Any) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO llm_calls(run_id,thread_id,purpose,provider,model,prompt_tokens,"
                "completion_tokens,total_tokens,latency_ms,ok,cost,ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    kw.get("run_id"), kw.get("thread_id"), kw.get("purpose"), kw.get("provider"),
                    kw.get("model"), kw.get("prompt_tokens", 0), kw.get("completion_tokens", 0),
                    kw.get("total_tokens", 0), kw.get("latency_ms", 0.0), int(bool(kw.get("ok", True))),
                    kw.get("cost", 0.0), time.time(),
                ),
            )

    def list_llm_calls(self, limit: int = 500) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM llm_calls ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]

    def llm_summary(self) -> dict[str, Any]:
        with self._conn() as c:
            total = c.execute(
                "SELECT COUNT(*) n, COALESCE(SUM(total_tokens),0) tok, COALESCE(SUM(cost),0) cost, "
                "COALESCE(AVG(latency_ms),0) lat FROM llm_calls"
            ).fetchone()
            by_model = c.execute(
                "SELECT model, COUNT(*) calls, COALESCE(SUM(total_tokens),0) tokens, COALESCE(SUM(cost),0) cost, "
                "COALESCE(AVG(latency_ms),0) avg_latency_ms FROM llm_calls GROUP BY model ORDER BY tokens DESC"
            ).fetchall()
            return {
                "calls": total["n"], "tokens": total["tok"], "cost": total["cost"],
                "avg_latency_ms": total["lat"], "by_model": [dict(r) for r in by_model],
            }

    def record_feedback(self, run_id, thread_id, rating: int, note: str = "") -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO llm_feedback(run_id,thread_id,rating,note,ts) VALUES(?,?,?,?,?)",
                (run_id, thread_id, rating, note, time.time()),
            )

    def feedback_summary(self) -> dict[str, Any]:
        with self._conn() as c:
            r = c.execute(
                "SELECT COUNT(*) n, COALESCE(SUM(CASE WHEN rating>0 THEN 1 ELSE 0 END),0) up, "
                "COALESCE(SUM(CASE WHEN rating<0 THEN 1 ELSE 0 END),0) down FROM llm_feedback"
            ).fetchone()
            return {"total": r["n"], "up": r["up"], "down": r["down"]}

    def record_run(self, **kwargs: Any) -> None:
        # Local sqlite mode: per-call rows already captured via record_llm_call.
        pass
