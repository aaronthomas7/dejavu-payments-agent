"""SQLite storage for the case queue, a retain outbox and a diagnosis log."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    case_id     TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    status      TEXT NOT NULL,           -- open | resolved
    source      TEXT NOT NULL,           -- history | live
    data        TEXT NOT NULL,           -- full case JSON (includes ground truth)
    resolution  TEXT                     -- JSON written when an analyst resolves the case
);
CREATE TABLE IF NOT EXISTS outbox (           -- retains that failed (e.g. network) and must be retried
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id     TEXT NOT NULL,
    payload     TEXT NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS diagnoses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id     TEXT NOT NULL,
    used_memory INTEGER NOT NULL,
    root_cause  TEXT NOT NULL,
    confidence  REAL NOT NULL,
    created_at  TEXT NOT NULL,
    payload     TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ---- cases -------------------------------------------------------------
    def load_if_empty(self, history: list[dict], live: list[dict]) -> int:
        with self._lock:
            n = self._conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
            if n:
                return 0
            rows = [(c["case_id"], c["created_at"], "resolved", "history", json.dumps(c), None) for c in history]
            rows += [(c["case_id"], c["created_at"], "open", "live", json.dumps(c), None) for c in live]
            self._conn.executemany("INSERT INTO cases VALUES (?,?,?,?,?,?)", rows)
            self._conn.commit()
            return len(rows)

    def list_cases(self, status: Optional[str] = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM cases" + (" WHERE status = ?" if status else "") + " ORDER BY created_at DESC"
        with self._lock:
            rows = self._conn.execute(q, (status,) if status else ()).fetchall()
        return [self._row(r) for r in rows]

    def get_case(self, case_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            r = self._conn.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()
        return self._row(r) if r else None

    def resolve_case(self, case_id: str, resolution: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute("UPDATE cases SET status = 'resolved', resolution = ? WHERE case_id = ?",
                               (json.dumps({**resolution, "resolved_at": _now()}), case_id))
            self._conn.commit()

    def reopen_live_cases(self) -> list[str]:
        with self._lock:
            ids = [r[0] for r in self._conn.execute("SELECT case_id FROM cases WHERE source='live' AND status='resolved'")]
            self._conn.execute("UPDATE cases SET status = 'open', resolution = NULL WHERE source = 'live'")
            self._conn.commit()
        return ids

    @staticmethod
    def _row(r: sqlite3.Row) -> dict[str, Any]:
        case = json.loads(r["data"])
        case["status"] = r["status"]
        case["source"] = r["source"]
        case["resolution"] = json.loads(r["resolution"]) if r["resolution"] else None
        return case

    # ---- outbox ------------------------------------------------------------
    def enqueue_retain(self, case_id: str, payload: dict[str, Any], error: str) -> None:
        with self._lock:
            self._conn.execute("INSERT INTO outbox (case_id, payload, attempts, last_error, created_at) VALUES (?,?,?,?,?)",
                               (case_id, json.dumps(payload), 1, error[:500], _now()))
            self._conn.commit()

    def pending_retains(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM outbox ORDER BY id").fetchall()
        return [{"id": r["id"], "case_id": r["case_id"], "payload": json.loads(r["payload"]), "attempts": r["attempts"]}
                for r in rows]

    def outbox_done(self, row_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM outbox WHERE id = ?", (row_id,))
            self._conn.commit()

    def outbox_failed(self, row_id: int, error: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE outbox SET attempts = attempts + 1, last_error = ? WHERE id = ?",
                               (error[:500], row_id))
            self._conn.commit()

    # ---- diagnosis log -----------------------------------------------------
    def log_diagnosis(self, case_id: str, used_memory: bool, root_cause: str, confidence: float, payload: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO diagnoses (case_id, used_memory, root_cause, confidence, created_at, payload) VALUES (?,?,?,?,?,?)",
                (case_id, int(used_memory), root_cause, confidence, _now(), json.dumps(payload)))
            self._conn.commit()

    def counts(self) -> dict[str, int]:
        with self._lock:
            open_n = self._conn.execute("SELECT COUNT(*) FROM cases WHERE status='open'").fetchone()[0]
            res_n = self._conn.execute("SELECT COUNT(*) FROM cases WHERE status='resolved'").fetchone()[0]
            out_n = self._conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
        return {"open": open_n, "resolved": res_n, "pending_retains": out_n}
