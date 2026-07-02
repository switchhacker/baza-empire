"""Cron health infra DB — heartbeats, delta-hashing, FYI queue, alert dedup.

SQLite at dashboard/cron_health.db. Separate from baza_projects.db (and from
claw_reviews.db) to avoid lock contention with the dashboard and other cron
writers. Every cron-improvements script (heartbeats, digest dedup, alerting)
builds on top of this module.

Mirrors the house DB-module pattern from core/claw_review_db.py.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

_THIS_DIR = Path(__file__).resolve().parent
_FRAMEWORK_DIR = _THIS_DIR.parent
DB_PATH = Path(
    os.environ.get("BAZA_CRON_HEALTH_DB")
    or str(_FRAMEWORK_DIR / "dashboard" / "cron_health.db")
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS cron_runs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  cron_name   TEXT NOT NULL,
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  status      TEXT,
  duration_s  REAL,
  error       TEXT,
  host        TEXT DEFAULT 'baza'
);
CREATE TABLE IF NOT EXISTS report_hashes (
  cron_name    TEXT PRIMARY KEY,
  last_hash    TEXT,
  last_sent_at TEXT
);
CREATE TABLE IF NOT EXISTS fyi_queue (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  cron_name     TEXT,
  priority      TEXT DEFAULT 'fyi',
  message       TEXT,
  created_at    TEXT,
  release_after TEXT,
  consumed_at   TEXT
);
CREATE TABLE IF NOT EXISTS cron_alert_state (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  key           TEXT UNIQUE,
  first_seen    TEXT,
  last_seen     TEXT,
  acked_at      TEXT,
  snoozed_until TEXT,
  meta          TEXT
);
CREATE INDEX IF NOT EXISTS idx_cron_runs_name    ON cron_runs(cron_name, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_fyi_release       ON fyi_queue(release_after, consumed_at);
CREATE INDEX IF NOT EXISTS idx_alert_state_key   ON cron_alert_state(key);
"""

VALID_STATUS = {"ok", "error", "timeout"}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    """Open a connection to the cron health DB. WAL mode, Row factory, 5s busy timeout.

    Caller owns the connection's lifetime. ``with connect() as conn: ...``
    only commits (on success) or rolls back (on exception) -- that's
    sqlite3.Connection's own context-manager protocol, and it does NOT close
    the connection. Close explicitly (``conn.close()``) or wrap with
    ``contextlib.closing()``:

        from contextlib import closing
        with closing(connect()) as conn:
            ...
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def _conn():
    """Internal helper: connect, commit on success, always close."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    """Create all four tables (+ indexes) if they don't exist. Idempotent."""
    with _conn() as conn:
        conn.executescript(SCHEMA)


# ── Run heartbeats ────────────────────────────────────────────────────────────

def record_run_start(cron_name: str) -> int:
    """Insert a cron_runs row for a starting run. Returns the new run id."""
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO cron_runs (cron_name, started_at) VALUES (?, ?)",
            (cron_name, _now_iso()),
        )
        return cur.lastrowid


def record_run_end(run_id: int, status: str, error: str | None = None) -> None:
    """Mark a run finished. status in {'ok','error','timeout'}. Sets finished_at + duration_s.

    Raises ValueError if status is not one of VALID_STATUS.
    """
    if status not in VALID_STATUS:
        raise ValueError(
            f"invalid status {status!r}; must be one of {sorted(VALID_STATUS)}"
        )
    finished_at = _now_iso()
    with _conn() as conn:
        row = conn.execute(
            "SELECT started_at FROM cron_runs WHERE id = ?", (run_id,)
        ).fetchone()
        duration_s = None
        if row and row["started_at"]:
            try:
                started = datetime.fromisoformat(row["started_at"])
                finished = datetime.fromisoformat(finished_at)
                duration_s = (finished - started).total_seconds()
            except ValueError:
                duration_s = None
        conn.execute(
            """UPDATE cron_runs
               SET finished_at = ?, status = ?, duration_s = ?, error = ?
               WHERE id = ?""",
            (finished_at, status, duration_s, error, run_id),
        )


def recent_runs(limit: int = 200, cron_name: str | None = None) -> list[sqlite3.Row]:
    """Most recent runs, newest first. Optionally filtered to one cron_name."""
    q = "SELECT * FROM cron_runs"
    args: list = []
    if cron_name:
        q += " WHERE cron_name = ?"
        args.append(cron_name)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with _conn() as conn:
        return conn.execute(q, args).fetchall()


def last_runs_by_cron() -> dict[str, sqlite3.Row]:
    """Newest run row per cron_name."""
    with _conn() as conn:
        rows = conn.execute(
            """SELECT cr.* FROM cron_runs cr
               INNER JOIN (
                   SELECT cron_name, MAX(id) AS max_id
                   FROM cron_runs
                   GROUP BY cron_name
               ) latest ON cr.cron_name = latest.cron_name AND cr.id = latest.max_id"""
        ).fetchall()
    return {row["cron_name"]: row for row in rows}


# ── Delta hashing (dedup unchanged reports) ───────────────────────────────────

def delta_changed(cron_name: str, body: str, force_after_h: float = 72.0) -> bool:
    """True (and records hash+now) when body's sha256 differs from the stored
    hash for cron_name, OR the last recorded send is older than force_after_h
    hours. False otherwise (no write happens in the False case).

    The read-compare-write is wrapped in a BEGIN IMMEDIATE transaction so
    overlapping callers serialize instead of racing: a second caller's
    SELECT can't start until the first caller's transaction (SELECT +
    conditional write) has committed, so it always compares against
    up-to-date data instead of a stale snapshot (which could otherwise let
    two concurrent callers both see changed=True and both fire a downstream
    send for the same change).
    """
    new_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    now = _now_iso()
    with _conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT last_hash, last_sent_at FROM report_hashes WHERE cron_name = ?",
            (cron_name,),
        ).fetchone()

        changed = True
        if row is not None:
            changed = row["last_hash"] != new_hash
            if not changed and row["last_sent_at"]:
                try:
                    last_sent = datetime.fromisoformat(row["last_sent_at"])
                    if datetime.now() - last_sent >= timedelta(hours=force_after_h):
                        changed = True
                except ValueError:
                    changed = True

        if changed:
            conn.execute(
                """INSERT OR REPLACE INTO report_hashes (cron_name, last_hash, last_sent_at)
                   VALUES (?, ?, ?)""",
                (cron_name, new_hash, now),
            )
        return changed


# ── FYI queue ──────────────────────────────────────────────────────────────────

def enqueue_fyi(cron_name: str, message: str, release_after: str) -> int:
    """Queue a low-priority FYI message to be released (delivered) at/after
    release_after (ISO 8601 string). Returns the new row id."""
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO fyi_queue (cron_name, priority, message, created_at, release_after)
               VALUES (?, 'fyi', ?, ?, ?)""",
            (cron_name, message, _now_iso(), release_after),
        )
        return cur.lastrowid


def pending_fyis(now_iso: str) -> list[sqlite3.Row]:
    """FYIs whose release_after <= now_iso and that haven't been consumed yet."""
    with _conn() as conn:
        return conn.execute(
            """SELECT * FROM fyi_queue
               WHERE release_after <= ? AND consumed_at IS NULL
               ORDER BY id ASC""",
            (now_iso,),
        ).fetchall()


def mark_fyis_consumed(ids: list[int]) -> None:
    """Stamp consumed_at on the given fyi_queue row ids."""
    if not ids:
        return
    placeholders = ",".join("?" * len(ids))
    with _conn() as conn:
        conn.execute(
            f"UPDATE fyi_queue SET consumed_at = ? WHERE id IN ({placeholders})",
            [_now_iso(), *ids],
        )


# ── Alert dedup / ack / snooze ────────────────────────────────────────────────

def should_alert(
    key: str,
    renotify_hours: float | None = None,
    meta: dict | None = None,
) -> tuple[bool, int]:
    """Upsert a cron_alert_state row for key. Returns (send_now, row_id).

    send_now is False when: acked_at is set, or snoozed_until is in the future,
    or (renotify_hours is set and last_seen is within renotify_hours of now).
    Always bumps last_seen (and meta, when a new meta dict is provided).

    New-key race: two concurrent callers can both SELECT and see row=None for
    the same fresh key before either INSERTs. To avoid an unhandled
    sqlite3.IntegrityError on the UNIQUE(key) constraint, the INSERT uses
    ON CONFLICT(key) DO NOTHING. Whichever caller's INSERT actually lands
    (cursor.rowcount == 1) owns the fresh row and returns (True, row_id)
    immediately. The caller that lost the race re-SELECTs the winner's row
    and falls through to the normal existing-row logic below, so it still
    returns sanely instead of raising.
    """
    now = _now_iso()
    now_dt = datetime.now()
    meta_json = json.dumps(meta) if meta is not None else None

    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM cron_alert_state WHERE key = ?", (key,)
        ).fetchone()

        if row is None:
            cur = conn.execute(
                """INSERT INTO cron_alert_state (key, first_seen, last_seen, meta)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(key) DO NOTHING""",
                (key, now, now, meta_json),
            )
            if cur.rowcount == 1:
                # We won the race (or there was no race): row is ours, fresh.
                return True, cur.lastrowid

            # Lost the race: another caller's INSERT already landed. Re-SELECT
            # and fall through to the existing-row logic below.
            row = conn.execute(
                "SELECT * FROM cron_alert_state WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                # Extremely unlikely (row deleted between the conflicting
                # INSERT and this re-SELECT); treat as sanely as possible
                # rather than raising.
                return True, -1

        row_id = row["id"]
        send_now = True

        if row["acked_at"]:
            send_now = False
        elif row["snoozed_until"]:
            try:
                snoozed_until = datetime.fromisoformat(row["snoozed_until"])
                if snoozed_until > now_dt:
                    send_now = False
            except ValueError:
                pass

        if send_now and renotify_hours is not None and row["last_seen"]:
            try:
                last_seen = datetime.fromisoformat(row["last_seen"])
                if now_dt - last_seen < timedelta(hours=renotify_hours):
                    send_now = False
            except ValueError:
                pass

        if meta is not None:
            conn.execute(
                "UPDATE cron_alert_state SET last_seen = ?, meta = ? WHERE id = ?",
                (now, meta_json, row_id),
            )
        else:
            conn.execute(
                "UPDATE cron_alert_state SET last_seen = ? WHERE id = ?",
                (now, row_id),
            )
        return send_now, row_id


def alert_ack(row_id: int) -> None:
    """Acknowledge an alert — future should_alert() calls for its key return False."""
    with _conn() as conn:
        conn.execute(
            "UPDATE cron_alert_state SET acked_at = ? WHERE id = ?",
            (_now_iso(), row_id),
        )


def alert_snooze(row_id: int, hours: float = 24.0) -> None:
    """Snooze an alert for `hours` — should_alert() returns False until it expires."""
    until = (datetime.now() + timedelta(hours=hours)).isoformat(timespec="seconds")
    with _conn() as conn:
        conn.execute(
            "UPDATE cron_alert_state SET snoozed_until = ? WHERE id = ?",
            (until, row_id),
        )


def alert_get(row_id: int) -> Optional[sqlite3.Row]:
    """Fetch a single cron_alert_state row by id, or None."""
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM cron_alert_state WHERE id = ?", (row_id,)
        ).fetchone()


if __name__ == "__main__":
    init()
    print(f"initialized {DB_PATH}")
