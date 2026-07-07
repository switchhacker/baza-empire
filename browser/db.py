"""SQLite state for the Phantom Browser service (:8100): crawl jobs + pages
and the short-TTL page cache.

DB lives at dashboard/phantom_browser.db (override: PHANTOM_BROWSER_DB env).
House idiom (see core/cron_health_db.py): WAL, Row factory, 5s timeout,
context-managed commit, idempotent init().
"""
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

_FRAMEWORK_DIR = Path(__file__).resolve().parent.parent

SCHEMA = """
CREATE TABLE IF NOT EXISTS crawl_jobs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  root_url    TEXT NOT NULL,
  params      TEXT NOT NULL DEFAULT '{}',
  status      TEXT NOT NULL DEFAULT 'pending',
  error       TEXT,
  created_at  REAL NOT NULL,
  finished_at REAL
);
CREATE TABLE IF NOT EXISTS crawl_pages (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id     INTEGER NOT NULL REFERENCES crawl_jobs(id),
  url        TEXT NOT NULL,
  title      TEXT,
  markdown   TEXT,
  status     TEXT NOT NULL DEFAULT 'ok',
  error      TEXT,
  fetched_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_crawl_pages_job ON crawl_pages(job_id);
CREATE TABLE IF NOT EXISTS page_cache (
  url        TEXT PRIMARY KEY,
  fetched_at REAL NOT NULL,
  payload    TEXT NOT NULL
);
"""


def _db_path() -> Path:
    return Path(
        os.environ.get("PHANTOM_BROWSER_DB")
        or str(_FRAMEWORK_DIR / "dashboard" / "phantom_browser.db")
    )


def connect() -> sqlite3.Connection:
    p = _db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def _conn():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with _conn() as c:
        c.executescript(SCHEMA)


# ── crawl jobs ────────────────────────────────────────────────────────────

def create_job(root_url: str, params: dict) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO crawl_jobs (root_url, params, created_at) VALUES (?,?,?)",
            (root_url, json.dumps(params), time.time()),
        )
        return cur.lastrowid


def get_job(job_id: int):
    with _conn() as c:
        row = c.execute("SELECT * FROM crawl_jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None


def set_job_status(job_id: int, status: str, error: str | None = None) -> None:
    finished = time.time() if status in ("done", "error") else None
    with _conn() as c:
        c.execute(
            "UPDATE crawl_jobs SET status=?, error=?, finished_at=COALESCE(?, finished_at) WHERE id=?",
            (status, error, finished, job_id),
        )


def add_page(job_id: int, url: str, title, markdown, status: str = "ok", error=None) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO crawl_pages (job_id, url, title, markdown, status, error, fetched_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (job_id, url, title, markdown, status, error, time.time()),
        )


def job_pages(job_id: int) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM crawl_pages WHERE job_id=? ORDER BY id", (job_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def requeue_running() -> list[int]:
    """On service startup: any job left 'running' by a crash/restart goes back
    to 'pending' so the server can relaunch it."""
    with _conn() as c:
        rows = c.execute("SELECT id FROM crawl_jobs WHERE status='running'").fetchall()
        ids = [r["id"] for r in rows]
        if ids:
            c.execute(
                f"UPDATE crawl_jobs SET status='pending' WHERE id IN ({','.join('?'*len(ids))})",
                ids,
            )
        return ids


# ── page cache ────────────────────────────────────────────────────────────

def cache_get(url: str, ttl: int = 900):
    with _conn() as c:
        row = c.execute("SELECT * FROM page_cache WHERE url=?", (url,)).fetchone()
        if not row or time.time() - row["fetched_at"] > ttl:
            return None
        return json.loads(row["payload"])


def cache_put(url: str, payload: dict) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO page_cache (url, fetched_at, payload) VALUES (?,?,?) "
            "ON CONFLICT(url) DO UPDATE SET fetched_at=excluded.fetched_at, payload=excluded.payload",
            (url, time.time(), json.dumps(payload)),
        )
