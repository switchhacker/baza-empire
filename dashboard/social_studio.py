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


# Routes are added in later tasks.
