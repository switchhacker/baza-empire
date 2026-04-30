"""SQLite connection + schema bootstrap for vision.db."""
from __future__ import annotations

import os
import sqlite3
from typing import Optional

DASHBOARD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(DASHBOARD_DIR, "vision.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
  id            INTEGER PRIMARY KEY,
  abs_path      TEXT NOT NULL UNIQUE,
  source        TEXT NOT NULL,                       -- 'inbound'|'scraped'|'generated'|'crop'
  origin_agent  TEXT,
  origin_url    TEXT,
  parent_id     INTEGER REFERENCES assets(id),
  width         INTEGER,
  height        INTEGER,
  bytes         INTEGER,
  sha256        TEXT,
  mtime         REAL,
  created_at    REAL,
  classified_at REAL,
  status        TEXT NOT NULL DEFAULT 'pending',     -- 'pending'|'ok'|'failed'|'rejected'
  error         TEXT
);
CREATE INDEX IF NOT EXISTS idx_assets_status ON assets(status);
CREATE INDEX IF NOT EXISTS idx_assets_source ON assets(source);
CREATE INDEX IF NOT EXISTS idx_assets_sha    ON assets(sha256);
CREATE INDEX IF NOT EXISTS idx_assets_parent ON assets(parent_id);

CREATE TABLE IF NOT EXISTS attributes (
  asset_id   INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  key        TEXT NOT NULL,
  value      TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 1.0,
  source     TEXT NOT NULL DEFAULT 'qwen3-vl',
  PRIMARY KEY (asset_id, key)
);
CREATE INDEX IF NOT EXISTS idx_attrs_kv ON attributes(key, value);

CREATE TABLE IF NOT EXISTS captions (
  asset_id INTEGER PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
  caption  TEXT,
  tags     TEXT,
  model    TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS assets_fts USING fts5(
  caption, tags, attrs_blob,
  content='', tokenize='porter unicode61'
);

CREATE TABLE IF NOT EXISTS crops (
  asset_id  INTEGER PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
  part      TEXT NOT NULL,
  bbox_x    INTEGER, bbox_y INTEGER,
  bbox_w    INTEGER, bbox_h INTEGER,
  detector  TEXT
);
CREATE INDEX IF NOT EXISTS idx_crops_part ON crops(part);

CREATE TABLE IF NOT EXISTS seed_demand (
  id            INTEGER PRIMARY KEY,
  taxonomy_path TEXT NOT NULL,
  needed        INTEGER NOT NULL DEFAULT 6,
  reason        TEXT,
  requested_at  REAL,
  fulfilled_at  REAL,
  fulfilled_by  TEXT
);
CREATE INDEX IF NOT EXISTS idx_seed_open ON seed_demand(fulfilled_at, requested_at);

CREATE TABLE IF NOT EXISTS gpu_lease (
  gpu         TEXT PRIMARY KEY,
  holder      TEXT NOT NULL,
  acquired_at REAL NOT NULL,
  expires_at  REAL NOT NULL,
  purpose     TEXT
);

CREATE TABLE IF NOT EXISTS ingest_log (
  id          INTEGER PRIMARY KEY,
  asset_id    INTEGER REFERENCES assets(id),
  step        TEXT NOT NULL,
  ok          INTEGER NOT NULL,
  duration_ms INTEGER,
  detail      TEXT,
  ts          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_log_ts ON ingest_log(ts);
"""


def connect(path: Optional[str] = None) -> sqlite3.Connection:
    """Open a connection with foreign keys ON and a sensible busy timeout."""
    p = path or DEFAULT_DB_PATH
    con = sqlite3.connect(p, timeout=30, isolation_level=None)  # autocommit
    con.execute("PRAGMA foreign_keys = ON;")
    con.execute("PRAGMA journal_mode = WAL;")
    con.execute("PRAGMA synchronous = NORMAL;")
    con.row_factory = sqlite3.Row
    return con


def init_db(path: Optional[str] = None) -> sqlite3.Connection:
    """Create the schema if missing. Idempotent."""
    con = connect(path)
    con.executescript(SCHEMA)
    return con
