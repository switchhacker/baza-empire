"""Isolated SQLite store for baza gate (faces + events). Like claw_reviews.db."""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS gate_gallery (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  person    TEXT NOT NULL,
  role      TEXT NOT NULL,            -- login_unlock | door | agent_voice
  embedding BLOB NOT NULL,            -- float32[512], np.ndarray.tobytes()
  added_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_gallery_person ON gate_gallery(person);
CREATE TABLE IF NOT EXISTS gate_events (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  ts      TEXT NOT NULL,
  node    TEXT,
  kind    TEXT NOT NULL,              -- grant | deny | auth | security | presence
  verdict TEXT,
  person  TEXT,
  score   REAL,
  detail  TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON gate_events(ts DESC, id DESC);
"""


def _db_path() -> Path:
    return Path(os.environ.get(
        "BAZA_GATE_DB",
        str(Path(__file__).resolve().parent.parent / "dashboard" / "gate.db"),
    ))


@contextmanager
def _conn():
    p = _db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), timeout=10.0)
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


def add_face(person: str, role: str, embedding: np.ndarray) -> None:
    blob = np.asarray(embedding, dtype=np.float32).tobytes()
    with _conn() as c:
        c.execute(
            "INSERT INTO gate_gallery (person, role, embedding, added_at) VALUES (?,?,?,?)",
            (person, role, blob, datetime.now(timezone.utc).isoformat()),
        )


def gallery() -> list[dict]:
    """One row per person with the set of roles they're enrolled for."""
    with _conn() as c:
        rows = c.execute(
            "SELECT person, role, MAX(added_at) AS added_at "
            "FROM gate_gallery GROUP BY person, role ORDER BY person"
        ).fetchall()
    by_person: dict[str, dict] = {}
    for r in rows:
        d = by_person.setdefault(r["person"], {"person": r["person"], "roles": [], "added_at": r["added_at"]})
        d["roles"].append(r["role"])
        d["added_at"] = max(d["added_at"], r["added_at"])
    return list(by_person.values())


def gallery_embeddings(role: str | None = None) -> list[tuple[str, str, np.ndarray]]:
    q = "SELECT person, role, embedding FROM gate_gallery"
    args: tuple = ()
    if role is not None:
        q += " WHERE role = ?"
        args = (role,)
    with _conn() as c:
        rows = c.execute(q, args).fetchall()
    return [(r["person"], r["role"], np.frombuffer(r["embedding"], dtype=np.float32).copy()) for r in rows]


def delete_person(person: str) -> int:
    with _conn() as c:
        cur = c.execute("DELETE FROM gate_gallery WHERE person = ?", (person,))
        return cur.rowcount


def log_event(node: str | None = None, kind: str = "", verdict: str | None = None,
              person: str | None = None, score: float | None = None,
              detail: str | None = None) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO gate_events (ts, node, kind, verdict, person, score, detail) "
            "VALUES (?,?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), node, kind, verdict, person, score, detail),
        )


def recent_events(limit: int = 50) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT ts, node, kind, verdict, person, score, detail "
            "FROM gate_events ORDER BY ts DESC, id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    return [dict(r) for r in rows]
