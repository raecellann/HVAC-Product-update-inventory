"""SQLite-backed run history.

Schema is deliberately tiny — the heavy per-run data (preview table, uploaded
file, problems.csv) lives on disk at RUNS_DIR/{run_id}/.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from core.config import HISTORY_DB, RUNS_DIR

DETAIL_RETENTION_DAYS = 90


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    HISTORY_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(HISTORY_DB, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    with _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at      TEXT    NOT NULL,
                kind            TEXT    NOT NULL,
                file_name       TEXT,
                status          TEXT    NOT NULL,
                counts_json     TEXT,
                warnings_json   TEXT,
                pushed          INTEGER DEFAULT 0,
                user_errors     INTEGER DEFAULT 0,
                error           TEXT,
                parent_run_id   INTEGER,
                applied_at      TEXT,
                details_purged  INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs(created_at DESC);

            CREATE TABLE IF NOT EXISTS cache_meta (
                id              INTEGER PRIMARY KEY CHECK (id=1),
                refreshed_at    TEXT,
                sku_count       INTEGER,
                duplicate_count INTEGER
            );
            """
        )


def mark_interrupted_runs() -> int:
    """At startup, any run still in a live state was killed by a restart."""
    with _conn() as c:
        cur = c.execute(
            "UPDATE runs SET status='interrupted', "
            "error=COALESCE(error,'Interrupted by server restart') "
            "WHERE status IN ('queued','running')"
        )
        return cur.rowcount


def create_run(kind: str, file_name: str | None = None, parent_run_id: int | None = None) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO runs (created_at, kind, file_name, status, parent_run_id) "
            "VALUES (?, ?, ?, 'queued', ?)",
            (_utcnow_iso(), kind, file_name, parent_run_id),
        )
        return int(cur.lastrowid)


def set_status(run_id: int, status: str, error: str | None = None) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE runs SET status=?, error=COALESCE(?, error) WHERE id=?",
            (status, error, run_id),
        )


def record_counts(run_id: int, counts: dict[str, int], warnings: list[str] | None = None) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE runs SET counts_json=?, warnings_json=? WHERE id=?",
            (json.dumps(counts), json.dumps(warnings or []), run_id),
        )


def record_apply(run_id: int, pushed: int, user_errors: int) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE runs SET pushed=?, user_errors=? WHERE id=?",
            (pushed, user_errors, run_id),
        )


def mark_preview_applied(preview_run_id: int) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE runs SET applied_at=? WHERE id=?",
            (_utcnow_iso(), preview_run_id),
        )


def get_run(run_id: int) -> dict[str, Any] | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return _row_to_dict(row) if row else None


def list_runs(limit: int = 50) -> list[dict[str, Any]]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def set_cache_meta(sku_count: int, duplicate_count: int) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO cache_meta (id, refreshed_at, sku_count, duplicate_count) "
            "VALUES (1, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "refreshed_at=excluded.refreshed_at, "
            "sku_count=excluded.sku_count, "
            "duplicate_count=excluded.duplicate_count",
            (_utcnow_iso(), sku_count, duplicate_count),
        )


def get_cache_meta() -> dict[str, Any] | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM cache_meta WHERE id=1").fetchone()
        if not row:
            return None
        out = dict(row)
        refreshed = out.get("refreshed_at")
        if refreshed:
            delta = datetime.now(timezone.utc) - datetime.strptime(
                refreshed, "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=timezone.utc)
            out["days_old"] = delta.days
            out["is_stale"] = delta.days > 7
        return out


def purge_old_run_details(days: int = DETAIL_RETENTION_DAYS) -> int:
    """Delete the large per-run files for runs older than `days`."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    purged = 0
    with _conn() as c:
        rows = c.execute(
            "SELECT id FROM runs WHERE created_at < ? AND details_purged=0",
            (cutoff,),
        ).fetchall()
        for r in rows:
            run_id = r["id"]
            run_dir = RUNS_DIR / str(run_id)
            if run_dir.exists():
                shutil.rmtree(run_dir, ignore_errors=True)
            c.execute("UPDATE runs SET details_purged=1 WHERE id=?", (run_id,))
            purged += 1
    return purged


def details_available(run: dict[str, Any]) -> bool:
    if run.get("details_purged"):
        return False
    run_dir = RUNS_DIR / str(run["id"])
    return run_dir.exists()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    for k in ("counts_json", "warnings_json"):
        raw = out.pop(k, None)
        key = k.replace("_json", "")
        out[key] = json.loads(raw) if raw else ({} if key == "counts" else [])
    return out
