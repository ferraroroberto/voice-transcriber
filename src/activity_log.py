"""Persistent activity log (SQLite) — durable events, independent of the archive.

``archive/`` session folders (and their ``meta.json``) are pruned after the
configured retention window (default 30 days — see :mod:`src.archive`), so
they are the wrong place to keep a durable trail of what the app did. This
module is that trail: a single WAL-mode SQLite file in the fleet runtime-data
root (``<root>/voice-transcriber/activity.sqlite3`` — see
:mod:`src.runtime_data`) holding one
``events`` table — one row per discrete thing that happened (a session was
created, a transcription succeeded or failed, a polish call succeeded or
failed, a session was deleted). Modeled on the home-automation project's
``src/telemetry.py`` events table, narrowed to voice-transcriber's single
domain (no separate numeric-readings table — there is no continuous sensor
sampling here).

Retention is independent of, and much longer than, the archive's: events are
tiny (no audio, no full transcript text) and human-meaningful, so they are
kept for a full year by default and pruned separately on webapp boot.

    from src.activity_log import record_event
    record_event("transcribed", session_id=sid, source="webapp",
                 duration_seconds=12.3, word_count=42)

Writing must never break the action being recorded — every write failure is
caught and logged, never raised.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from src.runtime_data import runtime_db_path

logger = logging.getLogger("activity_log")

# Default DB location: the fleet runtime-data root (``C:\sqlite\voice-transcriber\``
# on Windows), not this repo's ``webapp/`` — the checkout drive here is a spinning
# HDD, and this store is written by an always-on service (project-scaffolding#243).
# ``VT_ACTIVITY_DB_PATH`` (env) still overrides it, and still outranks everything —
# the tests set it to keep a temp DB off the real one.
DEFAULT_DB_PATH = runtime_db_path(
    "voice-transcriber", "activity.sqlite3", env_var="VT_ACTIVITY_DB_PATH"
)

DEFAULT_RETENTION_DAYS = 365
_DAY_SECONDS = 86400


_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    ts               INTEGER NOT NULL,
    event_type       TEXT    NOT NULL,
    session_id       TEXT,
    source           TEXT,
    outcome          TEXT,
    duration_seconds REAL,
    word_count       INTEGER,
    error            TEXT,
    payload          TEXT
);
CREATE INDEX IF NOT EXISTS events_q ON events(event_type, ts);
"""


@contextmanager
def _connect(path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    """Open a WAL-mode connection, creating the schema if needed.

    The schema check runs on every connect (a cheap idempotent
    ``CREATE TABLE IF NOT EXISTS``) so every function in this module
    works standalone without requiring a separate :func:`init_db` call
    first — useful for callers (and tests) that only ever write/read
    one or two events per process.
    """
    target = Path(path) if path is not None else DEFAULT_DB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(_SCHEMA)
        yield conn
    finally:
        conn.close()


def init_db(path: Optional[Path] = None) -> None:
    """Create the events table and index if they do not exist (idempotent).

    Not strictly required before calling :func:`record_event` /
    :func:`read_events` (they self-initialize), but called explicitly on
    webapp boot for clarity and to fail fast if the DB path is unwritable.
    """
    with _connect(path):
        pass


def _dumps(value: Optional[Dict[str, Any]]) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str)


def _loads(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def record_event(
    event_type: str,
    *,
    session_id: Optional[str] = None,
    source: Optional[str] = None,
    outcome: str = "ok",
    duration_seconds: Optional[float] = None,
    word_count: Optional[int] = None,
    error: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    ts: Optional[int] = None,
    path: Optional[Path] = None,
) -> None:
    """Persist one discrete event. Never raises — logs a warning on failure.

    ``ts`` defaults to now (epoch seconds).
    """
    when = int(ts if ts is not None else time.time())
    try:
        with _connect(path) as conn:
            conn.execute(
                """
                INSERT INTO events (
                    ts, event_type, session_id, source, outcome,
                    duration_seconds, word_count, error, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    when, event_type, session_id, source, outcome,
                    duration_seconds, word_count, error, _dumps(payload),
                ),
            )
            conn.commit()
    except (OSError, sqlite3.Error) as exc:
        logger.warning(f"⚠️  Could not record activity event {event_type!r}: {exc}")


def _where(
    event_type: Optional[str], since: Optional[int], until: Optional[int]
) -> Tuple[str, List[Any]]:
    """Build the shared ``event_type``/``since``/``until`` filter clause.

    Returns ``(" WHERE ..." | "", params)`` — the clause is empty and
    ``params`` is ``[]`` when no filter is given. Shared by
    :func:`read_events` and :func:`count_events` so the filter semantics
    (and column names) live in exactly one place.
    """
    clauses: List[str] = []
    params: List[Any] = []
    if event_type is not None:
        clauses.append("event_type = ?")
        params.append(event_type)
    if since is not None:
        clauses.append("ts >= ?")
        params.append(int(since))
    if until is not None:
        clauses.append("ts < ?")
        params.append(int(until))
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def read_events(
    *,
    event_type: Optional[str] = None,
    since: Optional[int] = None,
    until: Optional[int] = None,
    limit: int = 200,
    path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Return events matching the (optional) filters, newest first."""
    where, params = _where(event_type, since, until)
    params.append(max(1, int(limit)))
    query = f"SELECT * FROM events{where} ORDER BY ts DESC LIMIT ?"
    with _connect(path) as conn:
        rows = conn.execute(query, params).fetchall()
    return [
        {
            "ts": int(r["ts"]),
            "event_type": r["event_type"],
            "session_id": r["session_id"],
            "source": r["source"],
            "outcome": r["outcome"],
            "duration_seconds": r["duration_seconds"],
            "word_count": r["word_count"],
            "error": r["error"],
            "payload": _loads(r["payload"]),
        }
        for r in rows
    ]


def count_events(
    *,
    event_type: Optional[str] = None,
    since: Optional[int] = None,
    until: Optional[int] = None,
    path: Optional[Path] = None,
) -> int:
    """Count events matching the (optional) filters — avoids fetching rows."""
    where, params = _where(event_type, since, until)
    query = f"SELECT COUNT(*) AS n FROM events{where}"
    with _connect(path) as conn:
        row = conn.execute(query, params).fetchone()
    return int(row["n"]) if row else 0


def prune_older_than(days: int = DEFAULT_RETENTION_DAYS, path: Optional[Path] = None) -> int:
    """Delete events older than ``days``. Returns the count removed."""
    cutoff = int(time.time()) - days * _DAY_SECONDS
    with _connect(path) as conn:
        cur = conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
        conn.commit()
        removed = cur.rowcount or 0
    if removed:
        logger.info(f"🧹 Pruned {removed} activity events older than {days} days")
    return removed
