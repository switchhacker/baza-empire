"""Social Media Studio Blueprint for ahb123.

Routes mount under /api/ahb/social/*. This file owns the schema migration
and the Flask blueprint. Render logic lives in social_render.py; settings
accessors live in social_settings.py.
"""
from __future__ import annotations

import os
import sqlite3
from typing import Optional

from flask import Blueprint

DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH_DEFAULT = os.path.join(DASHBOARD_DIR, "baza_projects.db")


def _db_path() -> str:
    return os.environ.get("BAZA_DASHBOARD_DB", DB_PATH_DEFAULT)


def _ensure_social_tables(db_path: Optional[str] = None) -> None:
    """Create ahb_social_* tables and indexes. Idempotent."""
    path = db_path or _db_path()
    try:
        con = sqlite3.connect(path, timeout=8.0)
        con.execute("PRAGMA busy_timeout = 8000")
        con.execute("""CREATE TABLE IF NOT EXISTS ahb_social_presets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            platform_targets TEXT NOT NULL DEFAULT '["tiktok","ig_reel","ig_feed_square"]',
            prompt_template TEXT,
            hashtag_pool TEXT,
            tone TEXT DEFAULT 'pro',
            length TEXT DEFAULT 'medium',
            style TEXT DEFAULT 'trade',
            music_style TEXT DEFAULT 'none',
            voiceover_style TEXT DEFAULT 'none',
            source_filter TEXT DEFAULT '{}',
            cadence TEXT DEFAULT 'off',
            n_per_week INTEGER DEFAULT 0,
            max_per_day INTEGER DEFAULT 1,
            auto_approve INTEGER DEFAULT 0,
            score_threshold INTEGER DEFAULT 75,
            last_run_at TEXT,
            next_run_at TEXT,
            active INTEGER DEFAULT 1,
            is_seed INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS ahb_social_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            preset_id INTEGER,
            project_id INTEGER,
            source_media_ids TEXT NOT NULL DEFAULT '[]',
            platform TEXT NOT NULL,
            variant TEXT NOT NULL,
            asset_path TEXT,
            cover_path TEXT,
            caption TEXT,
            hashtags TEXT,
            first_comment TEXT,
            status TEXT NOT NULL DEFAULT 'draft',
            score INTEGER,
            ai_meta TEXT DEFAULT '{}',
            render_params TEXT DEFAULT '{}',
            scheduled_at TEXT,
            posted_at TEXT,
            posted_url TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_social_posts_status ON ahb_social_posts(status)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_social_posts_project ON ahb_social_posts(project_id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_social_posts_scheduled ON ahb_social_posts(scheduled_at)")
        con.execute("""CREATE TABLE IF NOT EXISTS ahb_social_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER,
            kind TEXT NOT NULL,
            input TEXT NOT NULL DEFAULT '{}',
            output_path TEXT,
            status TEXT NOT NULL DEFAULT 'queued',
            error TEXT,
            model_used TEXT,
            tokens INTEGER,
            started_at TEXT,
            finished_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_social_jobs_status ON ahb_social_jobs(status)")
        con.commit()
        con.close()
    except sqlite3.OperationalError as e:
        print(f"[startup] _ensure_social_tables deferred — DB busy: {e}", flush=True)


social_bp = Blueprint("social_studio", __name__)


import json
from datetime import datetime
from flask import jsonify, request

try:
    from dashboard import social_settings as _settings
except ImportError:
    import social_settings as _settings


def _conn():
    con = sqlite3.connect(_db_path(), timeout=8.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 8000")
    return con


def _row_to_preset(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in ("platform_targets", "source_filter"):
        try:
            d[k] = json.loads(d[k]) if d.get(k) else []
        except Exception:
            d[k] = []
    return d


PRESET_WRITABLE = {
    "name", "description", "platform_targets", "prompt_template",
    "hashtag_pool", "tone", "length", "style", "music_style",
    "voiceover_style", "source_filter", "cadence", "n_per_week",
    "max_per_day", "auto_approve", "score_threshold", "active",
}


@social_bp.route("/api/ahb/social/presets", methods=["GET"])
def social_presets_list():
    con = _conn()
    try:
        rows = con.execute("SELECT * FROM ahb_social_presets ORDER BY id DESC").fetchall()
    finally:
        con.close()
    return jsonify({"items": [_row_to_preset(r) for r in rows]})


@social_bp.route("/api/ahb/social/presets", methods=["POST"])
def social_presets_create():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    cols, vals = ["name"], [name]
    for k, v in data.items():
        if k == "name" or k not in PRESET_WRITABLE:
            continue
        cols.append(k)
        vals.append(json.dumps(v) if isinstance(v, (list, dict)) else v)
    con = _conn()
    try:
        cur = con.execute(
            f"INSERT INTO ahb_social_presets ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            vals,
        )
        con.commit()
        pid = cur.lastrowid
    finally:
        con.close()
    return jsonify({"id": pid})


@social_bp.route("/api/ahb/social/presets/<int:pid>", methods=["PUT"])
def social_presets_update(pid: int):
    data = request.get_json(silent=True) or {}
    sets, vals = [], []
    for k, v in data.items():
        if k not in PRESET_WRITABLE:
            continue
        sets.append(f"{k}=?")
        vals.append(json.dumps(v) if isinstance(v, (list, dict)) else v)
    if not sets:
        return jsonify({"error": "no writable fields"}), 400
    sets.append("updated_at=?")
    vals.append(datetime.utcnow().isoformat(timespec="seconds"))
    vals.append(pid)
    con = _conn()
    try:
        con.execute(f"UPDATE ahb_social_presets SET {','.join(sets)} WHERE id=?", vals)
        con.commit()
    finally:
        con.close()
    return jsonify({"ok": True})


@social_bp.route("/api/ahb/social/presets/<int:pid>", methods=["DELETE"])
def social_presets_delete(pid: int):
    con = _conn()
    try:
        con.execute("DELETE FROM ahb_social_presets WHERE id=?", (pid,))
        con.commit()
    finally:
        con.close()
    return jsonify({"ok": True})


@social_bp.route("/api/ahb/social/sources", methods=["GET"])
def social_sources():
    project_id = request.args.get("project_id", type=int)
    media_type = request.args.get("type")  # 'photo' | 'video' | None
    q = (request.args.get("q") or "").strip().lower()
    days = request.args.get("days", type=int)
    limit = min(request.args.get("limit", default=200, type=int), 500)
    sql = "SELECT id, project_id, sub_path, caption, tags, indexed_at FROM image_captions WHERE 1=1"
    args = []
    if project_id is not None:
        sql += " AND project_id=?"; args.append(project_id)
    if q:
        sql += " AND (LOWER(caption) LIKE ? OR LOWER(tags) LIKE ?)"
        args += [f"%{q}%", f"%{q}%"]
    if days:
        sql += " AND date(indexed_at) >= date('now', ?)"; args.append(f"-{int(days)} days")
    if media_type == "video":
        sql += " AND (LOWER(sub_path) LIKE '%.mp4' OR LOWER(sub_path) LIKE '%.mov' OR LOWER(sub_path) LIKE '%.webm')"
    elif media_type == "photo":
        sql += " AND (LOWER(sub_path) LIKE '%.jpg' OR LOWER(sub_path) LIKE '%.jpeg' OR LOWER(sub_path) LIKE '%.png' OR LOWER(sub_path) LIKE '%.heic')"
    sql += " ORDER BY indexed_at DESC LIMIT ?"
    args.append(limit)
    con = _conn()
    try:
        rows = con.execute(sql, args).fetchall()
    finally:
        con.close()
    return jsonify({"items": [dict(r) for r in rows]})
