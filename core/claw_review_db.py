"""Claw Batto continuous-review storage.

SQLite at dashboard/claw_reviews.db. Separate from baza_projects.db to avoid
lock contention with the dashboard.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

DB_PATH = Path(__file__).resolve().parent.parent / "dashboard" / "claw_reviews.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
  id          TEXT PRIMARY KEY,
  ts          TEXT NOT NULL,
  cadence     TEXT NOT NULL,
  target_kind TEXT NOT NULL,
  target      TEXT NOT NULL,
  severity    TEXT NOT NULL,
  title       TEXT NOT NULL,
  body        TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'open',
  acked_at    TEXT,
  fixed_at    TEXT,
  meta_json   TEXT
);
CREATE TABLE IF NOT EXISTS labels (
  review_id TEXT NOT NULL,
  label     TEXT NOT NULL,
  PRIMARY KEY (review_id, label)
);
CREATE TABLE IF NOT EXISTS cursors (
  name  TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  ts    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reviews_ts        ON reviews(ts DESC);
CREATE INDEX IF NOT EXISTS idx_reviews_cadence   ON reviews(cadence);
CREATE INDEX IF NOT EXISTS idx_reviews_severity  ON reviews(severity);
CREATE INDEX IF NOT EXISTS idx_reviews_status    ON reviews(status);
CREATE INDEX IF NOT EXISTS idx_reviews_target    ON reviews(target_kind, target);
CREATE INDEX IF NOT EXISTS idx_labels_label      ON labels(label);
"""

VALID_SEVERITY = {"info", "warn", "bug", "regression", "security"}
VALID_KIND     = {"file", "commit", "service", "process", "test", "infra", "log"}
VALID_CADENCE  = {"fast", "medium", "slow", "hourly", "fs_event", "manual"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _review_id(target_kind: str, target: str, title: str, body: str) -> str:
    h = hashlib.sha256()
    h.update(target_kind.encode())
    h.update(b"\x00")
    h.update(target.encode())
    h.update(b"\x00")
    h.update(title.encode())
    h.update(b"\x00")
    h.update(body[:500].encode())
    return h.hexdigest()[:16]


@contextmanager
def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH), timeout=10.0)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db() -> None:
    with _conn() as c:
        c.executescript(SCHEMA)


def add_review(
    target_kind: str,
    target: str,
    severity: str,
    title: str,
    body: str,
    labels: Iterable[str] = (),
    cadence: str = "manual",
    meta: Optional[dict] = None,
) -> Optional[str]:
    """Insert a review. Returns id if new, None if duplicate (already open)."""
    if target_kind not in VALID_KIND:
        target_kind = "file"
    if severity not in VALID_SEVERITY:
        severity = "info"
    if cadence not in VALID_CADENCE:
        cadence = "manual"
    rid = _review_id(target_kind, target, title, body)
    with _conn() as c:
        row = c.execute(
            "SELECT id, status FROM reviews WHERE id = ?", (rid,)
        ).fetchone()
        if row and row["status"] == "open":
            return None
        c.execute(
            """INSERT OR REPLACE INTO reviews
               (id, ts, cadence, target_kind, target, severity, title, body, status, meta_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)""",
            (
                rid, _now_iso(), cadence, target_kind, target, severity,
                title, body, json.dumps(meta) if meta else None,
            ),
        )
        c.execute("DELETE FROM labels WHERE review_id = ?", (rid,))
        for lab in {l.strip().lower() for l in labels if l and l.strip()}:
            c.execute(
                "INSERT OR IGNORE INTO labels (review_id, label) VALUES (?, ?)",
                (rid, lab),
            )
    return rid


def get_cursor(name: str, default: str = "") -> str:
    with _conn() as c:
        row = c.execute("SELECT value FROM cursors WHERE name = ?", (name,)).fetchone()
        return row["value"] if row else default


def set_cursor(name: str, value: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO cursors (name, value, ts) VALUES (?, ?, ?)",
            (name, value, _now_iso()),
        )


def recent(limit: int = 50, severity: Optional[str] = None, status: str = "open") -> list[dict]:
    q = "SELECT * FROM reviews WHERE status = ?"
    args: list = [status]
    if severity:
        q += " AND severity = ?"
        args.append(severity)
    q += " ORDER BY ts DESC LIMIT ?"
    args.append(limit)
    with _conn() as c:
        rows = c.execute(q, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["labels"] = [
                row["label"] for row in c.execute(
                    "SELECT label FROM labels WHERE review_id = ?", (r["id"],)
                ).fetchall()
            ]
            out.append(d)
        return out


def severity_counts(since_ts: Optional[str] = None) -> dict[str, int]:
    q = "SELECT severity, COUNT(*) n FROM reviews WHERE status = 'open'"
    args: list = []
    if since_ts:
        q += " AND ts >= ?"
        args.append(since_ts)
    q += " GROUP BY severity"
    out = {k: 0 for k in VALID_SEVERITY}
    with _conn() as c:
        for row in c.execute(q, args).fetchall():
            out[row["severity"]] = row["n"]
    return out


if __name__ == "__main__":
    init_db()
    print(f"initialized {DB_PATH}")
    print("severity counts:", severity_counts())
