"""Social Media Studio Blueprint for ahb123.

Routes mount under /api/ahb/social/*. This file owns the schema migration
and the Flask blueprint. Render logic lives in social_render.py; settings
accessors live in social_settings.py.
"""
from __future__ import annotations

import io
import os
import sqlite3
import subprocess
import zipfile
from typing import Optional

from flask import Blueprint

DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH_DEFAULT = os.path.join(DASHBOARD_DIR, "baza_projects.db")


def _db_path() -> str:
    return os.environ.get("BAZA_DASHBOARD_DB", DB_PATH_DEFAULT)


def _ensure_social_tables(db_path: Optional[str] = None) -> None:
    """Create ahb_social_* tables and indexes. Idempotent."""
    path = db_path or _db_path()
    con = None
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
    except sqlite3.OperationalError as e:
        print(f"[startup] _ensure_social_tables deferred — DB busy: {e}", flush=True)
    finally:
        if con is not None:
            con.close()


social_bp = Blueprint("social_studio", __name__)


import json
from datetime import datetime
from flask import jsonify, request, send_file

try:
    from dashboard import social_settings as _settings
except ImportError:
    import social_settings as _settings


def _conn():
    con = sqlite3.connect(_db_path(), timeout=8.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 8000")
    return con


# Best-effort brand-kit bootstrap (idempotent — does nothing on re-runs once filled)
try:
    _sq = os.environ.get(
        "BAZA_SQ_BUNDLE",
        "/home/switchhacker/baza-empire/agent-framework-v3/proj-ahb123/sq_bundle",
    )
    if os.path.isdir(_sq):
        _settings.bootstrap_brand_from_sq_bundle(_sq)
except Exception as _e:
    print(f"[social] brand bootstrap skipped: {_e}", flush=True)


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
    # T8: approval workflow + recurring schedule fields.
    "requires_review", "schedule_dow", "schedule_time",
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


def _media_captions_db_path() -> str:
    """Media tab's image_captions.db (separate from baza_projects.db)."""
    return os.environ.get(
        "BAZA_MEDIA_CAPTIONS_DB",
        os.path.join(DASHBOARD_DIR, "image_captions.db"),
    )


def _query_composer_sources(media_type, q, days, project_id, limit):
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
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()
    out = []
    for r in rows:
        d = dict(r)
        d["origin"] = "composer"
        out.append(d)
    return out


def _query_media_tab_sources(media_type, q, days, limit):
    """Read the Media tab's separate image_captions.db. Returns rows with
    origin='media', no integer id (abs_path is the de-facto key)."""
    path = _media_captions_db_path()
    if not os.path.exists(path):
        return []
    sql = ("SELECT abs_path, project_id, sub_path, caption, tags, indexed_at "
           "FROM image_captions WHERE status='ok'")
    args = []
    if q:
        sql += " AND (LOWER(caption) LIKE ? OR LOWER(tags) LIKE ? OR LOWER(sub_path) LIKE ?)"
        args += [f"%{q}%", f"%{q}%", f"%{q}%"]
    if days:
        sql += " AND date(indexed_at) >= date('now', ?)"
        args.append(f"-{int(days)} days")
    if media_type == "video":
        sql += " AND (LOWER(sub_path) LIKE '%.mp4' OR LOWER(sub_path) LIKE '%.mov' OR LOWER(sub_path) LIKE '%.webm')"
    elif media_type == "photo":
        sql += (" AND (LOWER(sub_path) LIKE '%.jpg' OR LOWER(sub_path) LIKE '%.jpeg' "
                "OR LOWER(sub_path) LIKE '%.png' OR LOWER(sub_path) LIKE '%.heic')")
    sql += " ORDER BY indexed_at DESC LIMIT ?"
    args.append(limit)
    con = sqlite3.connect(path, timeout=4.0)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(sql, args).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()
    return [{
        "id": None,
        "abs_path": r["abs_path"],
        "project_id": r["project_id"],
        "sub_path": r["sub_path"],
        "caption": r["caption"],
        "tags": r["tags"],
        "indexed_at": r["indexed_at"],
        "origin": "media",
    } for r in rows]


def _query_data_hub_sources(media_type, q, limit):
    """ahb_files rows (Data Hub). Filter to images/videos by file_type or
    file extension."""
    sql = ("SELECT id, name, file_path, file_type, tags, category, project_id, created_at "
           "FROM ahb_files WHERE file_path IS NOT NULL AND file_path != ''")
    args = []
    if q:
        sql += " AND (LOWER(name) LIKE ? OR LOWER(tags) LIKE ? OR LOWER(category) LIKE ?)"
        args += [f"%{q}%", f"%{q}%", f"%{q}%"]
    if media_type == "video":
        sql += " AND (LOWER(file_path) LIKE '%.mp4' OR LOWER(file_path) LIKE '%.mov' OR LOWER(file_path) LIKE '%.webm' OR LOWER(file_type)='video')"
    elif media_type == "photo":
        sql += (" AND (LOWER(file_path) LIKE '%.jpg' OR LOWER(file_path) LIKE '%.jpeg' "
                "OR LOWER(file_path) LIKE '%.png' OR LOWER(file_path) LIKE '%.heic' "
                "OR LOWER(file_type)='image')")
    else:
        sql += (" AND (LOWER(file_path) LIKE '%.jpg' OR LOWER(file_path) LIKE '%.jpeg' "
                "OR LOWER(file_path) LIKE '%.png' OR LOWER(file_path) LIKE '%.heic' "
                "OR LOWER(file_path) LIKE '%.mp4' OR LOWER(file_path) LIKE '%.mov' "
                "OR LOWER(file_path) LIKE '%.webm' OR LOWER(file_type) IN ('image','video'))")
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    con = _conn()
    try:
        rows = con.execute(sql, args).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()
    return [{
        "id": None,
        "abs_path": r["file_path"],
        "project_id": r["project_id"],
        "sub_path": r["name"] or r["file_path"],
        "caption": r["name"] or "",
        "tags": r["tags"] or r["category"] or "",
        "indexed_at": r["created_at"],
        "origin": "data-hub",
        "data_hub_id": r["id"],
    } for r in rows]


def _indexed_at_sort_key(v) -> float:
    """Coerce a mixed indexed_at value to a comparable epoch float.

    The three source origins disagree on storage: the composer DB and Data Hub
    use ISO-8601 text, while the Media tab's image_captions.db stores an epoch
    float. Sorting the union with a raw key crashes (TypeError: '<' not
    supported between 'float' and 'str'), so normalize everything to a float.
    """
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (ValueError, OSError):
        return 0.0


@social_bp.route("/api/ahb/social/sources", methods=["GET"])
def social_sources():
    project_id = request.args.get("project_id", type=int)
    media_type = request.args.get("type")  # 'photo' | 'video' | None
    q = (request.args.get("q") or "").strip().lower()
    days = request.args.get("days", type=int)
    limit = min(request.args.get("limit", default=200, type=int), 500)
    # v2.1 T-bridge: origins=composer,media,data-hub (CSV; default = all)
    origins_raw = (request.args.get("origins") or "composer,media,data-hub").lower()
    origins = {s.strip() for s in origins_raw.split(",") if s.strip()}
    items = []
    if "composer" in origins:
        items.extend(_query_composer_sources(media_type, q, days, project_id, limit))
    if "media" in origins:
        items.extend(_query_media_tab_sources(media_type, q, days, limit))
    if "data-hub" in origins:
        items.extend(_query_data_hub_sources(media_type, q, limit))
    # Cap the union; we sorted within each query, but re-sort the union by
    # indexed_at desc so the freshest stuff floats to the top.
    items.sort(key=lambda d: _indexed_at_sort_key(d.get("indexed_at")), reverse=True)
    items = items[:limit]
    return jsonify({"items": items, "origins_returned": sorted(origins)})


# ---------------------------------------------------------------------------
# Media serving for the composer/source grid.
#
# The cloud thumb/serve routes (/api/cloud/thumb/<path:...>) take the path as a
# URL *segment*, which mangles absolute paths (the encoded leading slash gets
# collapsed by Flask, causing a 308 → wrong path), and they only allow the ZFS
# pool dirs — not the dashboard's artifacts/ or uploads/social where social
# sources actually live. These social-scoped routes take the path as a query
# param (no slash mangling) and validate against the social allowed-roots.
# ---------------------------------------------------------------------------
@social_bp.route("/api/ahb/social/media/serve", methods=["GET"])
def social_media_serve():
    p_abs = _resolve_social_media_arg(request.args.get("path", ""))
    if not p_abs:
        return jsonify({"error": "not found"}), 404
    return send_file(p_abs)


_SOCIAL_IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif", ".bmp")


@social_bp.route("/api/ahb/social/media/thumb", methods=["GET"])
def social_media_thumb():
    p_abs = _resolve_social_media_arg(request.args.get("path", ""))
    if not p_abs:
        return jsonify({"error": "not found"}), 404
    try:
        size = min(max(int(request.args.get("size", 300)), 50), 1600)
    except (TypeError, ValueError):
        size = 300
    ext = os.path.splitext(p_abs)[1].lower()
    if ext in _SOCIAL_IMG_EXTS:
        try:
            try:
                import pillow_heif
                pillow_heif.register_heif_opener()
            except ImportError:
                pass
            from PIL import Image
            img = Image.open(p_abs)
            img.thumbnail((size, size), Image.LANCZOS)
            if img.mode != "RGB":
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=82)
            buf.seek(0)
            return send_file(buf, mimetype="image/jpeg")
        except Exception:
            # Decoding/resize failed — serve the original so the tile still
            # shows something instead of a black box.
            return send_file(p_abs)
    # Videos and other non-images have no cheap inline thumbnail; the <img>
    # onerror handler in the grid degrades gracefully.
    return jsonify({"error": "no thumb"}), 404


# ---------------------------------------------------------------------------
# T16 — In-app image editor: per-source edits sidecar
# ---------------------------------------------------------------------------
def _edits_dir() -> str:
    return os.environ.get(
        "BAZA_SOCIAL_EDITS_DIR",
        os.path.join(DASHBOARD_DIR, "artifacts", "social", "edits"),
    )
ALLOWED_FILTER_PRESETS = {"none", "cinematic", "vibrant", "moody", "bw", "warm"}


def _edits_path(source_id: int) -> str:
    return os.path.join(_edits_dir(), f"{source_id}.json")


def _load_edits(source_id: int) -> dict:
    """Return the sidecar dict or {} if no edits exist."""
    path = _edits_path(source_id)
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _normalize_edits(raw: dict) -> dict:
    """Sanity-clamp incoming edit values to defensible ranges."""
    out = {}
    crop = raw.get("crop") if isinstance(raw.get("crop"), dict) else None
    if crop:
        try:
            cw = max(1, int(crop.get("w") or 0))
            ch = max(1, int(crop.get("h") or 0))
            cx = max(0, int(crop.get("x") or 0))
            cy = max(0, int(crop.get("y") or 0))
            out["crop"] = {"x": cx, "y": cy, "w": cw, "h": ch}
        except (TypeError, ValueError):
            pass
    try:
        rot = float(raw.get("rotate") or 0)
        # Clamp to (-360, 360); pipeline emits transpose for ±90/180/270, rotate for free
        rot = max(-359.99, min(rot, 359.99))
        if abs(rot) > 0.01:
            out["rotate"] = rot
    except (TypeError, ValueError):
        pass
    for k, lo, hi in (("brightness", -1.0, 1.0),
                       ("contrast",   -1.0, 1.0),
                       ("saturation", -1.0, 1.0)):
        v = raw.get(k)
        if v is None:
            continue
        try:
            fv = max(lo, min(float(v), hi))
            if abs(fv) > 0.001:
                out[k] = fv
        except (TypeError, ValueError):
            continue
    f = raw.get("filter")
    if f and f in ALLOWED_FILTER_PRESETS and f != "none":
        out["filter"] = f
    return out


def _source_exists(source_id: int) -> bool:
    con = _conn()
    try:
        try:
            r = con.execute(
                "SELECT 1 FROM image_captions WHERE id=?", (source_id,)
            ).fetchone()
        except sqlite3.OperationalError:
            return False
    finally:
        con.close()
    return r is not None


@social_bp.route("/api/ahb/social/sources/<int:sid>/edits", methods=["GET"])
def social_source_edits_get(sid: int):
    return jsonify({"id": sid, "edits": _load_edits(sid)})


@social_bp.route("/api/ahb/social/sources/<int:sid>/edits", methods=["POST"])
def social_source_edits_save(sid: int):
    if not _source_exists(sid):
        return jsonify({"error": "source not found"}), 404
    raw = request.get_json(silent=True) or {}
    edits = _normalize_edits(raw)
    os.makedirs(_edits_dir(), exist_ok=True)
    with open(_edits_path(sid), "w") as f:
        json.dump(edits, f, indent=2)
    return jsonify({"id": sid, "edits": edits, "applied": True})


@social_bp.route("/api/ahb/social/sources/<int:sid>/edits", methods=["DELETE"])
def social_source_edits_delete(sid: int):
    path = _edits_path(sid)
    existed = os.path.exists(path)
    if existed:
        try:
            os.remove(path)
        except OSError:
            pass
    return jsonify({"id": sid, "reverted": existed})


@social_bp.route("/api/ahb/social/sources/import-by-path", methods=["POST"])
def social_sources_import_by_path():
    """Materialize a Media-tab or Data-Hub entry into baza_projects.image_captions
    so the standard int-id flows (post creation → render) can use it.

    Body: {abs_path, caption?, tags?, origin?}
    Returns: {id, sub_path, ...}
    """
    data = request.get_json(silent=True) or {}
    abs_path = (data.get("abs_path") or "").strip()
    if not abs_path:
        return jsonify({"error": "abs_path required"}), 400
    if not os.path.exists(abs_path):
        return jsonify({"error": "file not found", "path": abs_path}), 404
    caption = (data.get("caption") or "").strip()
    tags = (data.get("tags") or "").strip()
    origin = (data.get("origin") or "imported").strip()
    con = _conn()
    try:
        # Make sure the table exists (uploads route also creates it best-effort)
        con.execute(
            """CREATE TABLE IF NOT EXISTS image_captions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER,
                sub_path TEXT NOT NULL UNIQUE,
                caption TEXT,
                tags TEXT,
                indexed_at TEXT
            )"""
        )
        # Reuse an existing row if the absolute path is already there
        existing = con.execute(
            "SELECT id, project_id, sub_path, caption, tags, indexed_at "
            "FROM image_captions WHERE sub_path=?",
            (abs_path,),
        ).fetchone()
        if existing:
            return jsonify({"id": existing["id"], "sub_path": existing["sub_path"],
                            "already_imported": True})
        cur = con.execute(
            "INSERT INTO image_captions (project_id, sub_path, caption, tags, indexed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (None, abs_path, caption or os.path.basename(abs_path),
             tags or origin, datetime.utcnow().isoformat(timespec="seconds")),
        )
        con.commit()
        new_id = cur.lastrowid
    finally:
        con.close()
    return jsonify({"id": new_id, "sub_path": abs_path, "already_imported": False})


POST_WRITABLE = {
    "preset_id", "project_id", "source_media_ids", "platform", "variant",
    "asset_path", "cover_path", "caption", "hashtags", "first_comment",
    "status", "score", "ai_meta", "render_params", "scheduled_at",
    "posted_at", "posted_url",
    "music_id", "voiceover_path", "subtitles_path", "lut_name",
}

ALLOWED_STATUSES = {
    "draft", "pending_review", "approved", "scheduled", "posted",
    "rejected", "failed",
}

ALLOWED_PLATFORMS = {
    "tiktok", "ig_reel", "ig_feed_square", "ig_feed_portrait", "ig_story",
}


def _row_to_post(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in ("source_media_ids", "ai_meta", "render_params"):
        try:
            d[k] = json.loads(d[k]) if d.get(k) else ([] if k == "source_media_ids" else {})
        except Exception:
            d[k] = [] if k == "source_media_ids" else {}
    return d


@social_bp.route("/api/ahb/social/posts", methods=["GET"])
def social_posts_list():
    status = request.args.get("status")
    platform = request.args.get("platform")
    project_id = request.args.get("project_id", type=int)
    tag = (request.args.get("tag") or "").strip()
    q = (request.args.get("q") or "").strip().lower()
    limit = min(request.args.get("limit", default=100, type=int), 500)
    offset = max(0, request.args.get("offset", default=0, type=int))
    if status and status not in ALLOWED_STATUSES:
        return jsonify({"error": "invalid status"}), 400
    if platform and platform not in ALLOWED_PLATFORMS:
        return jsonify({"error": "invalid platform"}), 400
    if tag:
        sql = (
            "SELECT p.* FROM ahb_social_posts p "
            "JOIN ahb_social_post_tags pt ON pt.post_id = p.id "
            "JOIN ahb_social_tags t ON t.id = pt.tag_id "
            "WHERE t.name=?"
        )
        args = [tag]
    else:
        sql = "SELECT * FROM ahb_social_posts p WHERE 1=1"
        args = []
    if status:
        sql += " AND p.status=?"; args.append(status)
    if platform:
        sql += " AND p.platform=?"; args.append(platform)
    if project_id is not None:
        sql += " AND p.project_id=?"; args.append(project_id)
    # Full-text / substring search: ignore queries shorter than 3 chars.
    use_q = len(q) >= 3
    fts_clause = None
    fts_arg = None
    if use_q:
        # FTS5 phrase match — wrap in double quotes so user input with
        # operator chars (& : * etc) is treated as a literal phrase.
        fts_arg = '"' + q.replace('"', '""') + '"'
        fts_clause = (
            " AND p.id IN (SELECT rowid FROM ahb_social_posts_fts "
            "WHERE ahb_social_posts_fts MATCH ?)"
        )
    sql += " ORDER BY p.id DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    con = _conn()
    try:
        if use_q:
            # Try FTS5 path: splice the MATCH subquery in before ORDER BY
            order_idx = sql.rfind(" ORDER BY ")
            fts_sql = sql[:order_idx] + fts_clause + sql[order_idx:]
            fts_args = args[:-2] + [fts_arg] + args[-2:]
            try:
                rows = con.execute(fts_sql, fts_args).fetchall()
            except sqlite3.OperationalError:
                # FTS5 table missing or syntax error: fall back to LIKE.
                like_clause = (
                    " AND (LOWER(p.caption) LIKE ? OR LOWER(p.hashtags) LIKE ?"
                    " OR LOWER(p.first_comment) LIKE ?)"
                )
                like_sql = sql[:order_idx] + like_clause + sql[order_idx:]
                wild = f"%{q}%"
                like_args = args[:-2] + [wild, wild, wild] + args[-2:]
                rows = con.execute(like_sql, like_args).fetchall()
        else:
            rows = con.execute(sql, args).fetchall()
        items = [_row_to_post(r) for r in rows]
        # Attach tags per post (single batched query)
        if items:
            ids = [it["id"] for it in items]
            placeholders = ",".join("?" * len(ids))
            tag_rows = con.execute(
                f"SELECT pt.post_id, t.id, t.name, t.color "
                f"FROM ahb_social_post_tags pt "
                f"JOIN ahb_social_tags t ON t.id = pt.tag_id "
                f"WHERE pt.post_id IN ({placeholders}) "
                f"ORDER BY t.name COLLATE NOCASE",
                ids,
            ).fetchall()
            by_post: dict = {}
            for tr in tag_rows:
                by_post.setdefault(tr["post_id"], []).append(
                    {"id": tr["id"], "name": tr["name"], "color": tr["color"]}
                )
            for it in items:
                it["tags"] = by_post.get(it["id"], [])
        else:
            for it in items:
                it["tags"] = []
    finally:
        con.close()
    return jsonify({"items": items})


@social_bp.route("/api/ahb/social/posts", methods=["POST"])
def social_posts_create():
    data = request.get_json(silent=True) or {}
    if data.get("platform") and data["platform"] not in ALLOWED_PLATFORMS:
        return jsonify({"error": "invalid platform"}), 400
    cols, vals = [], []
    for k, v in data.items():
        if k not in POST_WRITABLE:
            continue
        cols.append(k)
        vals.append(json.dumps(v) if isinstance(v, (list, dict)) else v)
    if "platform" not in cols or "variant" not in cols:
        return jsonify({"error": "platform and variant required"}), 400
    con = _conn()
    try:
        cur = con.execute(
            f"INSERT INTO ahb_social_posts ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
            vals,
        )
        con.commit()
        pid = cur.lastrowid
    finally:
        con.close()
    return jsonify({"id": pid})


@social_bp.route("/api/ahb/social/posts/<int:pid>", methods=["PATCH"])
def social_posts_patch(pid: int):
    data = request.get_json(silent=True) or {}
    if "status" in data and data["status"] not in ALLOWED_STATUSES:
        return jsonify({"error": "invalid status"}), 400
    if "platform" in data and data["platform"] not in ALLOWED_PLATFORMS:
        return jsonify({"error": "invalid platform"}), 400
    sets, vals = [], []
    for k, v in data.items():
        if k not in POST_WRITABLE:
            continue
        sets.append(f"{k}=?")
        vals.append(json.dumps(v) if isinstance(v, (list, dict)) else v)
    if not sets:
        return jsonify({"error": "no writable fields"}), 400
    sets.append("updated_at=?"); vals.append(datetime.utcnow().isoformat(timespec="seconds"))
    vals.append(pid)
    con = _conn()
    try:
        # Snapshot the prior row before the UPDATE so users can restore
        # to any previous state (and undo restores).
        prior = con.execute(
            "SELECT * FROM ahb_social_posts WHERE id=?", (pid,)
        ).fetchone()
        if prior is not None:
            snap = {k: prior[k] for k in prior.keys() if k != "id"}
            con.execute(
                "INSERT INTO ahb_social_post_versions (post_id, snapshot) VALUES (?, ?)",
                (pid, json.dumps(snap, default=str)),
            )
        con.execute(f"UPDATE ahb_social_posts SET {','.join(sets)} WHERE id=?", vals)
        # T8: log approval event ONLY if status actually changed.
        if "status" in data and prior is not None:
            new_status = data.get("status")
            old_status = prior["status"]
            if new_status and new_status != old_status:
                try:
                    from dashboard import social_workflow as _swf
                except ImportError:
                    import social_workflow as _swf
                _swf._log_approval_event(
                    con, pid, f"status:{new_status}", "serge", "",
                )
        con.commit()
    finally:
        con.close()
    return jsonify({"ok": True})


@social_bp.route("/api/ahb/social/posts/<int:pid>", methods=["DELETE"])
def social_posts_delete(pid: int):
    con = _conn()
    try:
        con.execute("DELETE FROM ahb_social_posts WHERE id=?", (pid,))
        con.commit()
    finally:
        con.close()
    return jsonify({"ok": True})


@social_bp.route("/api/ahb/social/jobs/<int:jid>", methods=["GET"])
def social_jobs_get(jid: int):
    con = _conn()
    try:
        r = con.execute("SELECT * FROM ahb_social_jobs WHERE id=?", (jid,)).fetchone()
    finally:
        con.close()
    if not r:
        return jsonify({"error": "not found"}), 404
    return jsonify(dict(r))


@social_bp.route("/api/ahb/social/jobs", methods=["GET"])
def social_jobs_list():
    post_id = request.args.get("post_id", type=int)
    status = request.args.get("status")
    sql = "SELECT * FROM ahb_social_jobs WHERE 1=1"
    args = []
    if post_id is not None:
        sql += " AND post_id=?"; args.append(post_id)
    if status:
        sql += " AND status=?"; args.append(status)
    sql += " ORDER BY id DESC LIMIT 200"
    con = _conn()
    try:
        rows = con.execute(sql, args).fetchall()
    finally:
        con.close()
    return jsonify({"items": [dict(r) for r in rows]})


import re
import urllib.request

OLLAMA_BASE = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")


def _call_ollama_chat(model: str, system: str, user: str,
                      temperature: float = 0.7, timeout: int = 60) -> str:
    """Minimal /api/chat call. Returns the assistant text content, or "" on failure."""
    body = json.dumps({
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "options": {"temperature": temperature},
    }).encode()
    req = urllib.request.Request(
        f"{OLLAMA_BASE}/api/chat", data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        return (data.get("message") or {}).get("content", "")
    except Exception as e:
        print(f"[social] ollama call failed ({model}): {e}", flush=True)
        return ""


def _pick_copy_model() -> str:
    s = _settings.load_settings()
    return s.get("default_copy_model") or "gpt-oss:20b"


def _extract_json_array(text: str) -> list:
    """Pull the first JSON array out of model output, tolerating code fences
    and nested arrays/objects via depth counting."""
    if not text:
        return []
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    try:
        v = json.loads(cleaned)
        if isinstance(v, list):
            return v
    except Exception:
        pass
    start = cleaned.find("[")
    if start < 0:
        return []
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(cleaned)):
        c = cleaned[i]
        if esc:
            esc = False
            continue
        if c == "\\":
            esc = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                try:
                    v = json.loads(cleaned[start:i + 1])
                    return v if isinstance(v, list) else []
                except Exception:
                    return []
    return []


def _extract_json_obj(text: str) -> dict:
    """Pull the first JSON object out of model output, tolerating code fences."""
    if not text:
        return {}
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    try:
        v = json.loads(cleaned)
        if isinstance(v, dict):
            return v
    except Exception:
        pass
    start = cleaned.find("{")
    if start < 0:
        return {}
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(cleaned)):
        c = cleaned[i]
        if esc:
            esc = False
            continue
        if c == "\\":
            esc = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    v = json.loads(cleaned[start:i + 1])
                    return v if isinstance(v, dict) else {}
                except Exception:
                    return {}
    return {}


def _sources_summary(source_ids: list) -> str:
    """Build a 1-paragraph fact base from image_captions rows for the model."""
    if not source_ids:
        return ""
    placeholders = ",".join("?" * len(source_ids))
    con = _conn()
    try:
        try:
            rows = con.execute(
                f"SELECT id, sub_path, caption, tags FROM image_captions WHERE id IN ({placeholders})",
                source_ids,
            ).fetchall()
        except sqlite3.OperationalError:
            return "(no captions available)"
    finally:
        con.close()
    parts = []
    for r in rows:
        cap = (r["caption"] or "").strip()
        tags = (r["tags"] or "").strip()
        parts.append(f"- {r['sub_path']}: {cap} [{tags}]")
    return "\n".join(parts) if parts else "(no captions available)"


@social_bp.route("/api/ahb/social/ai/caption", methods=["POST"])
def ai_caption():
    data = request.get_json(silent=True) or {}
    sys_prompt = _settings.load_prompt("caption_system")
    user = (
        f"Platform: {data.get('platform', 'ig_reel')}\n"
        f"Tone: {data.get('tone', 'pro')}\n"
        f"Length: {data.get('length', 'medium')}\n"
        f"Style: {data.get('style', 'trade')}\n"
        f"Source media:\n{_sources_summary(data.get('source_ids') or [])}\n"
    )
    model = data.get("model") or _pick_copy_model()
    text = _call_ollama_chat(model, sys_prompt, user, temperature=0.7).strip()
    return jsonify({"caption": text, "model": model})


@social_bp.route("/api/ahb/social/ai/hashtags", methods=["POST"])
def ai_hashtags():
    data = request.get_json(silent=True) or {}
    sys_prompt = _settings.load_prompt("hashtag_system")
    brand = _settings.load_brand_kit()
    floor = brand.get("hashtag_floor") or []
    user = (
        f"Caption: {data.get('caption', '')}\n"
        f"Platform: {data.get('platform', 'ig_reel')}\n"
        f"Branded floor (must include): {floor}\n"
        f"Target count: {data.get('count', 18)}\n"
    )
    model = data.get("model") or _pick_copy_model()
    raw = _call_ollama_chat(model, sys_prompt, user, temperature=0.4)
    tags = _extract_json_array(raw)
    for f in floor:
        if f not in tags:
            tags.append(f)
    # Deduplicate while preserving order
    seen = set()
    tags = [t for t in tags if not (t in seen or seen.add(t))]
    return jsonify({"hashtags": tags, "model": model})


@social_bp.route("/api/ahb/social/ai/hooks", methods=["POST"])
def ai_hooks():
    data = request.get_json(silent=True) or {}
    n = int(data.get("n") or 3)
    sys_prompt = _settings.load_prompt("hooks_system")
    user = (
        f"N: {n}\n"
        f"Source media:\n{_sources_summary(data.get('source_ids') or [])}\n"
    )
    model = data.get("model") or _pick_copy_model()
    raw = _call_ollama_chat(model, sys_prompt, user, temperature=0.9)
    hooks = _extract_json_array(raw)[:n]
    return jsonify({"hooks": hooks, "model": model})


@social_bp.route("/api/ahb/social/ai/score", methods=["POST"])
def ai_score():
    data = request.get_json(silent=True) or {}
    sys_prompt = _settings.load_prompt("score_system")
    user = (
        f"Platform: {data.get('platform', 'ig_reel')}\n"
        f"Caption:\n{data.get('caption', '')}\n"
        f"Hashtags: {data.get('hashtags', '')}\n"
    )
    model = data.get("model") or _pick_copy_model()
    raw = _call_ollama_chat(model, sys_prompt, user, temperature=0.2)
    obj = _extract_json_obj(raw)
    return jsonify({
        "score": int(obj.get("score") or 0),
        "notes": str(obj.get("notes") or ""),
        "model": model,
    })


@social_bp.route("/api/ahb/social/ai/translate", methods=["POST"])
def ai_translate():
    data = request.get_json(silent=True) or {}
    text = data.get("text", "")
    target = data.get("target_lang", "es")
    sys_prompt = (
        f"You are a translator. Translate the user's text into {target}. "
        f"Output only the translation. Preserve hashtags and emoji."
    )
    model = data.get("model") or _pick_copy_model()
    out = _call_ollama_chat(model, sys_prompt, text, temperature=0.2)
    return jsonify({"text": out.strip(), "model": model})


try:
    from dashboard import social_render as _render
except ImportError:
    import social_render as _render


def _resolve_media_paths_with_ids(source_media_ids: list) -> list:
    """Like _resolve_media_paths but returns [{id, path}] pairs in input order
    (with unresolvable entries silently dropped). Used by render workers that
    need to look up per-source sidecars."""
    flat = _resolve_media_paths(source_media_ids, _return_pairs=True)
    return flat


def _social_cloud_root() -> str:
    # Real cloud storage lives on the ZFS pool (see app.py CLOUD_STORAGE); the
    # old "/home/switchhacker/baza-cloud" default never existed.
    return os.environ.get("BAZA_CLOUD_ROOT", "/mnt/empirepool/cloud")


def _social_allowed_roots() -> tuple:
    """Filesystem roots a social media path may live under (traversal defense).
    Shared by the path resolver and the thumb/serve endpoints so they never
    drift out of sync."""
    roots = [
        os.path.abspath(_social_cloud_root()),
        # webcam/screen-capture uploads
        os.path.abspath(os.path.join(DASHBOARD_DIR, "uploads", "social")),
        # Data Hub uploads
        os.path.abspath(os.path.join(DASHBOARD_DIR, "uploads", "ahb")),
        # the dashboard's own artifacts (Sam's generated images, Phil's photos)
        os.path.abspath(os.path.join(DASHBOARD_DIR, "artifacts")),
        # ZFS pool cloud + media imports (icloud/generated)
        "/mnt/empirepool/cloud",
        "/mnt/empirepool/media",
    ]
    extra = os.environ.get("BAZA_MEDIA_EXTRA_ROOT")
    if extra:
        roots.append(os.path.abspath(extra))
    return tuple(roots)


def _social_path_allowed(p_abs: str) -> bool:
    return any(
        p_abs == root or p_abs.startswith(root + os.sep)
        for root in _social_allowed_roots()
    )


def _resolve_social_media_arg(raw: str) -> Optional[str]:
    """Validate a caller-supplied media path (abs or cloud-root-relative) and
    return its absolute path if it exists inside an allowed root, else None."""
    if not raw:
        return None
    p = raw if os.path.isabs(raw) else os.path.join(_social_cloud_root(), raw)
    p_abs = os.path.abspath(p)
    if not _social_path_allowed(p_abs) or not os.path.isfile(p_abs):
        return None
    return p_abs


def _resolve_media_paths(source_media_ids: list, _return_pairs: bool = False) -> list:
    """Map image_captions.id → absolute file path. Joins sub_path under the
    baza cloud root if not absolute. When _return_pairs is True, returns
    [{id, path}, ...] preserving input order; otherwise returns [path, ...]."""
    if not source_media_ids:
        return []
    placeholders = ",".join("?" * len(source_media_ids))
    con = _conn()
    try:
        try:
            rows = con.execute(
                f"SELECT id, sub_path FROM image_captions WHERE id IN ({placeholders})",
                source_media_ids,
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    finally:
        con.close()
    cloud_root = _social_cloud_root()
    allowed_roots = _social_allowed_roots()
    paths = []
    for r in rows:
        p = r["sub_path"]
        if not os.path.isabs(p):
            p = os.path.join(cloud_root, p)
        p_abs = os.path.abspath(p)
        # Reject if path escapes any of the allowed roots (traversal defense)
        if not any(
            p_abs.startswith(root + os.sep) or p_abs == root
            for root in allowed_roots
        ):
            continue
        if os.path.exists(p_abs):
            if _return_pairs:
                paths.append({"id": r["id"], "path": p_abs})
            else:
                paths.append(p_abs)
    # Preserve caller's input order when pairs are requested
    if _return_pairs:
        order = {sid: idx for idx, sid in enumerate(source_media_ids)}
        paths.sort(key=lambda d: order.get(d["id"], 1e9))
    return paths


@social_bp.route("/api/ahb/social/posts/<int:pid>/render", methods=["POST"])
def social_render_post(pid: int):
    body = request.get_json(silent=True) or {}
    con = _conn()
    try:
        row = con.execute("SELECT * FROM ahb_social_posts WHERE id=?", (pid,)).fetchone()
    finally:
        con.close()
    if not row:
        return jsonify({"error": "post not found"}), 404
    post = _row_to_post(row)
    pairs = _resolve_media_paths_with_ids(post["source_media_ids"])
    if not pairs:
        return jsonify({"error": "no resolvable source media"}), 400
    out_dir = os.path.join(
        DASHBOARD_DIR, "artifacts", "social",
        datetime.utcnow().strftime("%Y-%m-%d"), str(pid),
    )
    os.makedirs(out_dir, exist_ok=True)
    # T16: per-source edits
    edits_tmpdir = os.path.join(out_dir, "_edits")
    os.makedirs(edits_tmpdir, exist_ok=True)
    paths = []
    for pair in pairs:
        ed = _load_edits(pair["id"])
        p = _render.preprocess_with_edits(pair["path"], ed, edits_tmpdir) if ed else pair["path"]
        paths.append(p)
    is_video = any(
        p.lower().endswith((".mp4", ".mov", ".webm", ".mkv")) for p in paths
    )
    platform = post["platform"]
    ext = ".mp4" if is_video else ".jpg"
    out_path = os.path.join(out_dir, f"{platform}{ext}")
    hook = body.get("hook_text")
    fill = body.get("fill_mode", "blurred")
    try:
        if is_video:
            _render.render_video(paths, out_path, platform, hook_text=hook, fill_mode=fill)
            cover_path = os.path.join(out_dir, "cover.jpg")
            _render.extract_cover(out_path, cover_path)
        else:
            _render.render_still(paths[0], out_path, platform, hook_text=hook, fill_mode=fill)
            cover_path = out_path
    except (subprocess.CalledProcessError, ValueError, OSError) as e:
        con = _conn()
        try:
            con.execute("UPDATE ahb_social_posts SET status='failed' WHERE id=?", (pid,))
            con.commit()
        finally:
            con.close()
        detail = (e.stderr.decode(errors='ignore')[-500:]
                  if isinstance(e, subprocess.CalledProcessError) and e.stderr
                  else str(e))
        return jsonify({"error": "render failed", "detail": detail}), 500
    con = _conn()
    try:
        con.execute(
            "UPDATE ahb_social_posts SET asset_path=?, cover_path=?, updated_at=? WHERE id=?",
            (out_path, cover_path, datetime.utcnow().isoformat(timespec="seconds"), pid),
        )
        con.commit()
    finally:
        con.close()
    return jsonify({"ok": True, "asset_path": out_path, "cover_path": cover_path})


@social_bp.route("/api/ahb/social/posts/<int:pid>/cover", methods=["GET"])
def social_post_cover(pid: int):
    con = _conn()
    try:
        r = con.execute("SELECT cover_path FROM ahb_social_posts WHERE id=?", (pid,)).fetchone()
    finally:
        con.close()
    if r and r["cover_path"] and os.path.exists(r["cover_path"]):
        return send_file(r["cover_path"])
    # Fallback: an unrendered draft has no cover yet. Serve the first source
    # image so the library still shows a preview thumbnail instead of a blank.
    paths = _resolve_media_paths(_get_post_source_ids(pid))
    img_exts = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif")
    first_img = next((p for p in paths if p.lower().endswith(img_exts)), None)
    if first_img:
        return send_file(first_img)
    return jsonify({"error": "no cover"}), 404


@social_bp.route("/api/ahb/social/posts/<int:pid>/bundle", methods=["GET"])
def social_post_bundle(pid: int):
    con = _conn()
    try:
        r = con.execute("SELECT * FROM ahb_social_posts WHERE id=?", (pid,)).fetchone()
    finally:
        con.close()
    if not r:
        return jsonify({"error": "not found"}), 404
    post = _row_to_post(r)
    if not post.get("asset_path") or not os.path.exists(post["asset_path"]):
        return jsonify({"error": "no rendered asset"}), 400
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(post["asset_path"], arcname=os.path.basename(post["asset_path"]))
        if post.get("cover_path") and os.path.exists(post["cover_path"]):
            z.write(post["cover_path"], arcname="cover.jpg")
        caption_block = (post.get("caption") or "") + "\n\n" + (post.get("hashtags") or "")
        if post.get("first_comment"):
            caption_block += "\n\n---\n" + post["first_comment"]
        z.writestr(f"caption_{post['platform']}.txt", caption_block)
        # v2.1: write per-language caption files when translations are present
        translations_raw = post.get("translations") or "{}"
        try:
            tr = json.loads(translations_raw) if isinstance(translations_raw, str) else translations_raw
        except Exception:
            tr = {}
        if isinstance(tr, dict):
            for lang, payload in tr.items():
                if not isinstance(payload, dict):
                    continue
                block = (payload.get("caption") or "") + "\n\n" + (payload.get("hashtags") or "")
                z.writestr(f"caption_{post['platform']}.{lang}.txt", block)
        z.writestr("manifest.json", json.dumps(post, default=str, indent=2))
    buf.seek(0)
    return send_file(buf, mimetype="application/zip",
                     as_attachment=True, download_name=f"social_{pid}.zip")


SEED_PRESETS = [
    {"name": "Project Showcase", "tone": "pro", "length": "medium", "style": "showcase",
     "platform_targets": ["ig_feed_square", "ig_reel"], "is_seed": 1,
     "description": "6-10 best photos from one project as carousel + Reel."},
    {"name": "Before / After Reel", "tone": "hype", "length": "short", "style": "showcase",
     "platform_targets": ["tiktok", "ig_reel"], "is_seed": 1,
     "description": "Split-screen first vs final phase, 15s, hype tone."},
    {"name": "Heavy Equipment Spotlight", "tone": "educational", "length": "medium", "style": "showcase",
     "platform_targets": ["tiktok", "ig_reel"], "is_seed": 1,
     "description": "Single video, slow-mo intro, gear specs overlay."},
    {"name": "Process Explainer", "tone": "educational", "length": "medium", "style": "tutorial",
     "platform_targets": ["tiktok", "ig_reel"], "is_seed": 1,
     "description": "30-60s how-we-do-it w/ voiceover."},
    {"name": "Customer Testimonial", "tone": "pro", "length": "medium", "style": "showcase",
     "platform_targets": ["ig_reel", "ig_feed_square"], "is_seed": 1,
     "description": "Quote pulled from Reviews + branded card."},
    {"name": "Day-in-the-Life", "tone": "casual", "length": "medium", "style": "behind",
     "platform_targets": ["tiktok", "ig_reel"], "is_seed": 1,
     "description": "Montage from one day's media, music-led."},
    {"name": "Quick Tip", "tone": "educational", "length": "short", "style": "tutorial",
     "platform_targets": ["tiktok", "ig_story"], "is_seed": 1,
     "description": "Single still + bold text overlay, 5-10 word hook."},
    {"name": "Sub / Trade Shout-out", "tone": "casual", "length": "short", "style": "behind",
     "platform_targets": ["ig_feed_square", "ig_story"], "is_seed": 1,
     "description": "Tag a sub w/ photo of their work."},
]


@social_bp.route("/api/ahb/social/presets/install-seeds", methods=["POST"])
def social_presets_install_seeds():
    con = _conn()
    try:
        existing = {r[0] for r in con.execute(
            "SELECT name FROM ahb_social_presets WHERE is_seed=1")}
        inserted = []
        for sp in SEED_PRESETS:
            if sp["name"] in existing:
                continue
            cols = list(sp.keys())
            vals = [json.dumps(v) if isinstance(v, (list, dict)) else v for v in sp.values()]
            cur = con.execute(
                f"INSERT INTO ahb_social_presets ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
                vals,
            )
            inserted.append(cur.lastrowid)
        con.commit()
    finally:
        con.close()
    return jsonify({"installed": inserted})


@social_bp.route("/api/ahb/social/settings", methods=["GET"])
def social_settings_get():
    return jsonify(_settings.load_settings())


@social_bp.route("/api/ahb/social/settings", methods=["PUT"])
def social_settings_put():
    data = request.get_json(silent=True) or {}
    s = _settings.load_settings()
    s.update({k: v for k, v in data.items() if k in s})
    _settings.save_settings(s)
    return jsonify({"ok": True, "settings": s})


def _ensure_social_v2_tables(db_path: Optional[str] = None) -> None:
    """Add v2 column additions and tables. Idempotent."""
    path = db_path or _db_path()
    con = None
    try:
        con = sqlite3.connect(path, timeout=8.0)
        con.execute("PRAGMA busy_timeout = 8000")
        for table, col_def in [
            ("ahb_social_jobs",    "pid INTEGER"),
            ("ahb_social_posts",   "translations TEXT DEFAULT '{}'"),
            ("ahb_social_posts",   "music_id INTEGER"),
            ("ahb_social_posts",   "voiceover_path TEXT"),
            ("ahb_social_posts",   "subtitles_path TEXT"),
            ("ahb_social_posts",   "lut_name TEXT"),
        ]:
            try:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
            except sqlite3.OperationalError:
                pass
        con.execute("""CREATE TABLE IF NOT EXISTS ahb_social_music_library (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL UNIQUE,
            title TEXT,
            artist TEXT,
            license_url TEXT,
            bpm INTEGER,
            key_signature TEXT,
            duration_seconds REAL,
            mood TEXT,
            tags TEXT,
            indexed_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_music_library_mood ON ahb_social_music_library(mood)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_music_library_bpm ON ahb_social_music_library(bpm)")
        con.commit()
    except sqlite3.OperationalError as e:
        print(f"[startup] _ensure_social_v2_tables deferred: {e}", flush=True)
    finally:
        if con is not None:
            con.close()


_ensure_social_v2_tables()


import threading


def _kick_render_async(post_id: int, body: dict) -> int:
    con = _conn()
    try:
        cur = con.execute(
            "INSERT INTO ahb_social_jobs (post_id, kind, status, input) VALUES (?, ?, ?, ?)",
            (post_id, "render", "queued", json.dumps(body)),
        )
        con.commit()
        job_id = cur.lastrowid
    finally:
        con.close()

    def _worker():
        con = _conn()
        try:
            con.execute(
                "UPDATE ahb_social_jobs SET status='running', started_at=?, pid=? WHERE id=?",
                (datetime.utcnow().isoformat(timespec="seconds"), os.getpid(), job_id),
            )
            con.commit()
        finally:
            con.close()
        try:
            source_ids = _get_post_source_ids(post_id)
            pairs = _resolve_media_paths_with_ids(source_ids)
            if not pairs:
                _job_finish(job_id, "failed", error="no resolvable source media")
                return
            out_dir = os.path.join(
                DASHBOARD_DIR, "artifacts", "social",
                datetime.utcnow().strftime("%Y-%m-%d"), str(post_id),
            )
            os.makedirs(out_dir, exist_ok=True)
            # T16: apply per-source edits (crop/rotate/eq/filter) before everything
            edits_tmpdir = os.path.join(out_dir, "_edits")
            os.makedirs(edits_tmpdir, exist_ok=True)
            paths = []
            source_ids = []  # rebuild in resolved order
            for pair in pairs:
                src_id = pair["id"]
                src_path = pair["path"]
                edits = _load_edits(src_id)
                if edits:
                    src_path = _render.preprocess_with_edits(src_path, edits, edits_tmpdir)
                paths.append(src_path)
                source_ids.append(src_id)
            is_video = any(
                p.lower().endswith((".mp4", ".mov", ".webm", ".mkv")) for p in paths
            )
            post = _get_post(post_id)
            if post is None:
                _job_finish(job_id, "failed", error="post not found")
                return
            platform = post["platform"]
            ext = ".mp4" if is_video else ".jpg"
            out_path = os.path.join(out_dir, f"{platform}{ext}")
            hook = (body or {}).get("hook_text")
            fill = (body or {}).get("fill_mode", "blurred")
            trims = (body or {}).get("trims") or {}
            ken_burns = bool((body or {}).get("ken_burns", True))
            beat_sync = bool((body or {}).get("beat_sync", False))
            try:
                if is_video:
                    post_row = _get_post(post_id)
                    music_id = post_row["music_id"] if post_row else None
                    music_path = None
                    if music_id:
                        con2 = _conn()
                        try:
                            m = con2.execute("SELECT path FROM ahb_social_music_library WHERE id=?", (music_id,)).fetchone()
                        finally:
                            con2.close()
                        if m and os.path.exists(m["path"]):
                            music_path = m["path"]
                    voiceover_path = post_row["voiceover_path"] if post_row and post_row["voiceover_path"] and os.path.exists(post_row["voiceover_path"]) else None
                    subtitles_path = post_row["subtitles_path"] if post_row and post_row["subtitles_path"] and os.path.exists(post_row["subtitles_path"]) else None
                    lut_name = post_row["lut_name"] if post_row else None
                    lut_path = None
                    if lut_name:
                        candidate = os.path.join(DASHBOARD_DIR, "static", "social", "luts", f"{lut_name}.cube")
                        if os.path.exists(candidate):
                            lut_path = candidate
                    brand = _settings.load_brand_kit()
                    logo_path = None
                    logo_rel = brand.get("logo_path")
                    if logo_rel:
                        full = os.path.join(DASHBOARD_DIR, logo_rel) if not os.path.isabs(logo_rel) else logo_rel
                        if os.path.exists(full):
                            logo_path = full
                    intro_path = brand.get("intro_clip_path")
                    outro_path = brand.get("outro_clip_path")
                    if trims:
                        clip_list = []
                        for sid, p in zip(source_ids, paths):
                            t = trims.get(str(sid)) or {}
                            clip_list.append({
                                "path": p,
                                "in_seconds": t.get("in_seconds"),
                                "out_seconds": t.get("out_seconds"),
                            })
                        _render.render_video(clip_list, out_path, platform,
                                             hook_text=hook, fill_mode=fill,
                                             lut_path=lut_path, logo_path=logo_path,
                                             subtitles_path=subtitles_path,
                                             music_path=music_path,
                                             voiceover_path=voiceover_path,
                                             intro_path=intro_path, outro_path=outro_path,
                                             ken_burns=ken_burns, beat_sync=beat_sync)
                    else:
                        _render.render_video(paths, out_path, platform,
                                             hook_text=hook, fill_mode=fill,
                                             lut_path=lut_path, logo_path=logo_path,
                                             subtitles_path=subtitles_path,
                                             music_path=music_path,
                                             voiceover_path=voiceover_path,
                                             intro_path=intro_path, outro_path=outro_path,
                                             ken_burns=ken_burns, beat_sync=beat_sync)
                    cover_path = os.path.join(out_dir, "cover.jpg")
                    _render.extract_cover(out_path, cover_path)
                else:
                    _render.render_still(paths[0], out_path, platform, hook_text=hook, fill_mode=fill)
                    cover_path = out_path
            except (subprocess.CalledProcessError, ValueError, OSError) as e:
                _set_post_status(post_id, "failed")
                detail = (e.stderr.decode(errors='ignore')[-500:]
                          if isinstance(e, subprocess.CalledProcessError) and e.stderr
                          else str(e))
                _job_finish(job_id, "failed", error=detail, output_path=None)
                return
            _set_post_render_paths(post_id, out_path, cover_path)
            _job_finish(job_id, "done", output_path=out_path)
        except Exception as e:
            _job_finish(job_id, "failed", error=str(e))

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return job_id


def _get_post_source_ids(post_id: int) -> list:
    con = _conn()
    try:
        r = con.execute("SELECT source_media_ids FROM ahb_social_posts WHERE id=?", (post_id,)).fetchone()
    finally:
        con.close()
    if not r:
        return []
    try:
        return json.loads(r["source_media_ids"] or "[]")
    except Exception:
        return []


def _get_post(post_id: int):
    con = _conn()
    try:
        r = con.execute("SELECT * FROM ahb_social_posts WHERE id=?", (post_id,)).fetchone()
    finally:
        con.close()
    return r


def _set_post_status(post_id: int, status: str) -> None:
    con = _conn()
    try:
        con.execute("UPDATE ahb_social_posts SET status=?, updated_at=? WHERE id=?",
                    (status, datetime.utcnow().isoformat(timespec="seconds"), post_id))
        con.commit()
    finally:
        con.close()


def _set_post_render_paths(post_id: int, asset_path: str, cover_path: str) -> None:
    con = _conn()
    try:
        con.execute(
            "UPDATE ahb_social_posts SET asset_path=?, cover_path=?, updated_at=? WHERE id=?",
            (asset_path, cover_path, datetime.utcnow().isoformat(timespec="seconds"), post_id),
        )
        con.commit()
    finally:
        con.close()


def _job_finish(job_id: int, status: str, error: str = None, output_path: str = None) -> None:
    con = _conn()
    try:
        con.execute(
            "UPDATE ahb_social_jobs SET status=?, finished_at=?, error=?, output_path=? WHERE id=?",
            (status, datetime.utcnow().isoformat(timespec="seconds"), error, output_path, job_id),
        )
        con.commit()
    finally:
        con.close()


@social_bp.route("/api/ahb/social/posts/<int:pid>/render-async", methods=["POST"])
def social_render_post_async(pid: int):
    body = request.get_json(silent=True) or {}
    if _get_post(pid) is None:
        return jsonify({"error": "post not found"}), 404
    job_id = _kick_render_async(pid, body)
    return jsonify({"job_id": job_id})


@social_bp.route("/api/ahb/social/jobs/<int:jid>", methods=["DELETE"])
def social_job_cancel(jid: int):
    con = _conn()
    try:
        row = con.execute("SELECT status, pid FROM ahb_social_jobs WHERE id=?", (jid,)).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        con.execute(
            "UPDATE ahb_social_jobs SET status='cancelled', finished_at=? WHERE id=?",
            (datetime.utcnow().isoformat(timespec="seconds"), jid),
        )
        con.commit()
    finally:
        con.close()
    return jsonify({"ok": True})


@social_bp.route("/api/ahb/social/brand-kit", methods=["GET"])
def social_brand_get():
    return jsonify(_settings.load_brand_kit())


@social_bp.route("/api/ahb/social/brand-kit", methods=["PUT"])
def social_brand_put():
    data = request.get_json(silent=True) or {}
    b = _settings.load_brand_kit()
    b.update({k: v for k, v in data.items() if k in b})
    _settings.save_brand_kit(b)
    return jsonify({"ok": True, "brand_kit": b})


_BRAND_UPLOAD_KINDS = {
    "logo":  {"field": "logo_path",  "exts": {".png"},               "max_mb": 1,  "max_seconds": None},
    "intro": {"field": "intro_clip_path", "exts": {".mp4", ".mov"}, "max_mb": 30, "max_seconds": 5.0},
    "outro": {"field": "outro_clip_path", "exts": {".mp4", ".mov"}, "max_mb": 30, "max_seconds": 5.0},
}


def _probe_video_duration(path: str) -> float:
    import subprocess

    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path],
            check=True, capture_output=True, timeout=15,
        )
        return float(out.stdout.decode().strip())
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError):
        return -1.0


@social_bp.route("/api/ahb/social/brand-kit/upload", methods=["POST"])
def social_brand_upload():
    kind = (request.form.get("kind") or "").strip().lower()
    if kind not in _BRAND_UPLOAD_KINDS:
        return jsonify({"error": "kind must be one of: " + ", ".join(_BRAND_UPLOAD_KINDS)}), 400
    spec = _BRAND_UPLOAD_KINDS[kind]
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "file required (multipart 'file')"}), 400
    fn = file.filename or ""
    ext = os.path.splitext(fn)[1].lower()
    if ext not in spec["exts"]:
        return jsonify({
            "error": f"{kind} requires extension in {sorted(spec['exts'])}",
        }), 400
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "static", "social", "brand")
    os.makedirs(out_dir, exist_ok=True)
    safe_name = f"{kind}{ext}"
    dest = os.path.join(out_dir, safe_name)
    # Stream + cap on size
    max_bytes = spec["max_mb"] * 1024 * 1024
    total = 0
    chunks = []
    while True:
        chunk = file.stream.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            return jsonify({"error": f"{kind} exceeds {spec['max_mb']}MB"}), 400
        chunks.append(chunk)
    with open(dest, "wb") as f:
        for c in chunks:
            f.write(c)
    # Duration validation for clips
    if spec["max_seconds"] is not None:
        dur = _probe_video_duration(dest)
        if dur < 0:
            os.remove(dest)
            return jsonify({"error": f"ffprobe failed to read {kind} duration"}), 400
        if dur > spec["max_seconds"]:
            os.remove(dest)
            return jsonify({
                "error": f"{kind} duration {dur:.1f}s exceeds max {spec['max_seconds']}s",
            }), 400
    # Persist on brand kit
    b = _settings.load_brand_kit()
    rel_path = os.path.join("static", "social", "brand", safe_name)
    b[spec["field"]] = rel_path
    _settings.save_brand_kit(b)
    return jsonify({"ok": True, "kind": kind, "path": rel_path, "size_bytes": total})


# ---------------------------------------------------------------------------
# Auto-Pilot helpers
# ---------------------------------------------------------------------------
from datetime import timedelta


def _today_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


def _count_posts_today(con, preset_id=None) -> int:
    if preset_id is not None:
        return con.execute(
            "SELECT COUNT(*) FROM ahb_social_posts WHERE date(created_at)=? AND preset_id=?",
            (_today_iso(), preset_id),
        ).fetchone()[0]
    return con.execute(
        "SELECT COUNT(*) FROM ahb_social_posts WHERE date(created_at)=?",
        (_today_iso(),),
    ).fetchone()[0]


def _next_run_from_cadence(cadence: str, n_per_week: int) -> str:
    now = datetime.utcnow()
    if cadence == "daily":
        return (now + timedelta(days=1)).isoformat(timespec="seconds")
    if cadence == "n_per_week" and n_per_week > 0:
        gap_hours = max(1, int(7 * 24 / n_per_week))
        return (now + timedelta(hours=gap_hours)).isoformat(timespec="seconds")
    if cadence == "on_trigger":
        return ""
    return ""


def _pick_sources_for_preset(con, source_filter: dict, cool_down_days: int) -> list:
    """Return a list of image_captions.id matching the preset's filter,
    excluding any used by a post within cool_down_days."""
    args = []
    sql = "SELECT id FROM image_captions WHERE 1=1"
    pids = source_filter.get("project_ids") or []
    if pids:
        placeholders = ",".join("?" * len(pids))
        sql += f" AND project_id IN ({placeholders})"
        args.extend(pids)
    sql += " ORDER BY indexed_at DESC LIMIT 12"
    try:
        candidates = [r[0] for r in con.execute(sql, args).fetchall()]
    except sqlite3.OperationalError:
        return []
    if candidates:
        used_rows = con.execute(
            "SELECT source_media_ids FROM ahb_social_posts "
            "WHERE created_at >= datetime('now', ?)",
            (f"-{int(cool_down_days)} days",),
        ).fetchall()
        used = set()
        for r in used_rows:
            try:
                used.update(json.loads(r[0] or "[]"))
            except Exception:
                pass
        candidates = [c for c in candidates if c not in used]
    return candidates


@social_bp.route("/api/ahb/social/autopilot/status", methods=["GET"])
def autopilot_status():
    s = _settings.load_settings()
    con = _conn()
    try:
        drafts_today = _count_posts_today(con)
    finally:
        con.close()
    return jsonify({
        "master": bool(s.get("autopilot_master")),
        "drafts_today": drafts_today,
        "daily_cap": s.get("daily_post_cap"),
    })


@social_bp.route("/api/ahb/social/autopilot/toggle", methods=["POST"])
def autopilot_toggle():
    on = bool((request.get_json(silent=True) or {}).get("on"))
    s = _settings.load_settings()
    s["autopilot_master"] = on
    _settings.save_settings(s)
    return jsonify({"ok": True, "master": on})


def _generate_one_post_from_preset(preset: dict, source_ids: list):
    """Build caption + hashtags + score for the preset using local Ollama,
    insert a row into ahb_social_posts, return the new post id."""
    platform = (preset.get("platform_targets") or ["ig_feed_square"])[0]
    sys_prompt = _settings.load_prompt("caption_system")
    summary = _sources_summary(source_ids)
    user = (
        f"Platform: {platform}\nTone: {preset.get('tone','pro')}\n"
        f"Length: {preset.get('length','medium')}\nStyle: {preset.get('style','trade')}\n"
        f"Source media:\n{summary}\n"
    )
    model = _pick_copy_model()
    caption = _call_ollama_chat(model, sys_prompt, user).strip()
    brand = _settings.load_brand_kit()
    raw = _call_ollama_chat(
        model, _settings.load_prompt("hashtag_system"),
        f"Caption: {caption}\nPlatform: {platform}\nFloor: {brand.get('hashtag_floor') or []}\n",
        temperature=0.4,
    )
    tags = _extract_json_array(raw)
    for f in (brand.get("hashtag_floor") or []):
        if f not in tags:
            tags.append(f)
    raw_s = _call_ollama_chat(
        model, _settings.load_prompt("score_system"),
        f"Platform: {platform}\nCaption:\n{caption}\nHashtags: {' '.join(tags)}\n",
        temperature=0.2,
    )
    score_obj = _extract_json_obj(raw_s)
    score = int(score_obj.get("score") or 0)
    status = "pending_review"
    if preset.get("auto_approve") and score >= int(preset.get("score_threshold") or 75):
        status = "approved"
    # T8: requires_review HARD-overrides auto_approve — anything generated
    # under a "needs human review" preset must wait in pending_review.
    if preset.get("requires_review"):
        status = "pending_review"
    con = _conn()
    try:
        cur = con.execute(
            """INSERT INTO ahb_social_posts
            (preset_id, source_media_ids, platform, variant, caption, hashtags,
             first_comment, status, score, ai_meta)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (preset["id"], json.dumps(source_ids), platform, platform,
             caption, " ".join(tags),
             brand.get("first_comment_floor") or "",
             status, score,
             json.dumps({"model": model, "notes": score_obj.get("notes", "")})),
        )
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def _preset_schedule_allows(preset: dict, now=None) -> bool:
    """T8: recurring schedule gate.

    `schedule_dow` is a CSV of integers in JS getDay() convention (0=Sun..6=Sat).
    `schedule_time` is "HH:MM" in UTC. If set, the tick must fire within
    +/-30min of that time. Both fields optional — absent means no gate.
    """
    now = now or datetime.utcnow()
    dow_csv = (preset.get("schedule_dow") or "").strip()
    if dow_csv:
        try:
            allowed = {int(x) for x in dow_csv.split(",") if x.strip() != ""}
        except ValueError:
            allowed = set()
        # Python weekday(): 0=Mon..6=Sun ; convert to JS getDay() 0=Sun..6=Sat.
        js_dow = (now.weekday() + 1) % 7
        if allowed and js_dow not in allowed:
            return False
    sched_t = (preset.get("schedule_time") or "").strip()
    if sched_t:
        try:
            hh, mm = sched_t.split(":")
            target_minutes = int(hh) * 60 + int(mm)
        except ValueError:
            return True  # malformed — don't block
        cur_minutes = now.hour * 60 + now.minute
        # ±30 min window, wrap-safe by checking min distance modulo 24h.
        diff = abs(cur_minutes - target_minutes)
        diff = min(diff, 24 * 60 - diff)
        if diff > 30:
            return False
    return True


def _publish_due_scheduled(con) -> list[int]:
    """Send posts whose scheduled_at has passed to Telegram and mark them posted.

    Runs regardless of the autopilot master toggle — these were scheduled
    explicitly by the user. scheduled_at is compared against local time since
    the UI's datetime-local picker stores naive local ISO strings.
    """
    now_local = datetime.now().isoformat(timespec="seconds")
    rows = con.execute(
        "SELECT id FROM ahb_social_posts WHERE status='scheduled' "
        "AND scheduled_at IS NOT NULL AND scheduled_at != '' AND scheduled_at <= ?",
        (now_local,),
    ).fetchall()
    published = []
    for r in rows:
        pid = r["id"]
        ok, err = _send_post_to_telegram(pid)
        if ok:
            con.execute(
                "UPDATE ahb_social_posts SET status='posted', updated_at=? WHERE id=?",
                (now_local, pid),
            )
            con.commit()
            published.append(pid)
        else:
            # Leave status='scheduled' so the next tick retries.
            print(f"[autopilot] scheduled post {pid} publish failed: {err}", flush=True)
    return published


@social_bp.route("/api/ahb/social/autopilot/tick", methods=["POST"])
def autopilot_tick():
    s = _settings.load_settings()
    published = []
    con = _conn()
    try:
        published = _publish_due_scheduled(con)
    finally:
        con.close()
    if not s.get("autopilot_master"):
        return jsonify({"ran": 0, "reason": "master off",
                        "published_scheduled": published})
    daily_cap = int(s.get("daily_post_cap") or 4)
    cool_days = int(s.get("cool_down_days") or 14)
    now_iso = datetime.utcnow().isoformat(timespec="seconds")
    con = _conn()
    try:
        drafts_today = _count_posts_today(con)
        if drafts_today >= daily_cap:
            return jsonify({"ran": 0, "reason": "daily cap"})
        due = con.execute(
            "SELECT * FROM ahb_social_presets WHERE active=1 AND cadence != 'off' "
            "AND (next_run_at IS NULL OR next_run_at = '' OR next_run_at <= ?) "
            "ORDER BY next_run_at",
            (now_iso,),
        ).fetchall()
        ran = []
        for r in due:
            if drafts_today >= daily_cap:
                break
            preset = _row_to_preset(r)
            if _count_posts_today(con, preset_id=preset["id"]) >= int(preset.get("max_per_day") or 1):
                continue
            # T8: recurring DOW / time-of-day gate (optional per preset).
            if not _preset_schedule_allows(preset):
                continue
            try:
                source_filter = preset.get("source_filter") or {}
                if isinstance(source_filter, str):
                    source_filter = json.loads(source_filter or "{}")
            except Exception:
                source_filter = {}
            sources = _pick_sources_for_preset(con, source_filter, cool_days)
            if not sources:
                continue
            try:
                pid = _generate_one_post_from_preset(preset, sources)
                ran.append(pid)
                drafts_today += 1
                con.execute(
                    "UPDATE ahb_social_presets SET last_run_at=?, next_run_at=?, updated_at=? WHERE id=?",
                    (now_iso,
                     _next_run_from_cadence(preset["cadence"], int(preset.get("n_per_week") or 0)),
                     now_iso, preset["id"]),
                )
                con.commit()
            except Exception as e:
                print(f"[autopilot] preset {preset['id']} failed: {e}", flush=True)
    finally:
        con.close()
    return jsonify({"ran": len(ran), "post_ids": ran,
                    "published_scheduled": published})


@social_bp.route("/api/ahb/social/presets/<int:pid>/run", methods=["POST"])
def social_preset_run(pid: int):
    con = _conn()
    try:
        r = con.execute("SELECT * FROM ahb_social_presets WHERE id=?", (pid,)).fetchone()
        if not r:
            return jsonify({"error": "not found"}), 404
        preset = _row_to_preset(r)
        cool_days = int(_settings.load_settings().get("cool_down_days") or 14)
        try:
            source_filter = preset.get("source_filter") or {}
            if isinstance(source_filter, str):
                source_filter = json.loads(source_filter or "{}")
        except Exception:
            source_filter = {}
        sources = _pick_sources_for_preset(con, source_filter, cool_days)
    finally:
        con.close()
    if not sources:
        return jsonify({"error": "no eligible sources"}), 400
    new_pid = _generate_one_post_from_preset(preset, sources)
    return jsonify({"post_id": new_pid})


def _send_post_to_telegram(pid: int) -> tuple[bool, str]:
    """Load post `pid`, build the Specter bridge payload, POST to /notify.

    Returns (ok, error_msg). On 404 or any failure, ok=False with a
    human-readable error string; on success, error is "".

    Shared by single-post `social_post_telegram` route and the bulk
    telegram action in social_workflow.
    """
    con = _conn()
    try:
        r = con.execute("SELECT * FROM ahb_social_posts WHERE id=?", (pid,)).fetchone()
    finally:
        con.close()
    if not r:
        return False, "not found"
    post = _row_to_post(r)
    payload = {
        "kind": "social_draft",
        "post_id": pid,
        "platform": post["platform"],
        "caption": post.get("caption") or "",
        "hashtags": post.get("hashtags") or "",
        "cover_path": post.get("cover_path"),
        "asset_path": post.get("asset_path"),
        "score": post.get("score"),
        "status": post.get("status"),
    }
    bridge = os.environ.get("BAZA_SPECTER_BRIDGE", "http://127.0.0.1:8765")
    try:
        req = urllib.request.Request(
            f"{bridge}/notify", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            ok = resp.status == 200
    except Exception as e:
        return False, f"bridge unavailable: {e}"
    return ok, "" if ok else "bridge returned non-200"


@social_bp.route("/api/ahb/social/posts/<int:pid>/telegram", methods=["POST"])
def social_post_telegram(pid: int):
    """Drop the bundle (or caption + cover) to Serge's Telegram via the
    Specter bridge /notify endpoint."""
    ok, err = _send_post_to_telegram(pid)
    if not ok and err == "not found":
        return jsonify({"error": "not found"}), 404
    if not ok and err.startswith("bridge unavailable"):
        return jsonify({"error": err}), 502
    return jsonify({"ok": ok})


try:
    from dashboard import social_ai, social_audio, social_sources
except ImportError:
    import social_ai
    import social_audio
    import social_sources
social_ai.register(social_bp)
social_audio.register(social_bp)
social_sources.register(social_bp)


def _ensure_social_v22_tables(db_path: Optional[str] = None) -> None:
    """Add v2.2 tables for workflow/trends/analytics. Idempotent."""
    path = db_path or _db_path()
    con = None
    try:
        con = sqlite3.connect(path, timeout=8.0)
        con.execute("PRAGMA busy_timeout = 8000")
        for col_def in [
            "requires_review INTEGER DEFAULT 0",
            "schedule_dow TEXT",
            "schedule_time TEXT",
        ]:
            try:
                con.execute(f"ALTER TABLE ahb_social_presets ADD COLUMN {col_def}")
            except sqlite3.OperationalError:
                pass
        # T11: library cleanup — archived_at on posts marks moved-to-archive items.
        for col_def in [
            "archived_at TEXT",
        ]:
            try:
                con.execute(f"ALTER TABLE ahb_social_posts ADD COLUMN {col_def}")
            except sqlite3.OperationalError:
                pass
        con.executescript("""
            CREATE TABLE IF NOT EXISTS ahb_social_post_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                caption_template TEXT,
                hashtag_set TEXT,
                platform_targets TEXT DEFAULT '[]',
                first_comment_template TEXT,
                music_id INTEGER,
                voiceover_script TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS ahb_social_tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                color TEXT DEFAULT '#10b981',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS ahb_social_post_tags (
                post_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                PRIMARY KEY (post_id, tag_id)
            );
            CREATE TABLE IF NOT EXISTS ahb_social_hashtag_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tag TEXT NOT NULL,
                observed_at TEXT DEFAULT CURRENT_TIMESTAMP,
                source_url TEXT,
                notes TEXT
            );
            CREATE TABLE IF NOT EXISTS ahb_social_competitors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                handle TEXT NOT NULL,
                platform TEXT NOT NULL,
                notes TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS ahb_social_sound_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sound_url TEXT,
                example_video_url TEXT,
                title TEXT,
                observed_at TEXT DEFAULT CURRENT_TIMESTAMP,
                notes TEXT
            );
            CREATE TABLE IF NOT EXISTS ahb_social_analytics (
                post_id INTEGER PRIMARY KEY,
                views INTEGER DEFAULT 0,
                likes INTEGER DEFAULT 0,
                comments INTEGER DEFAULT 0,
                saves INTEGER DEFAULT 0,
                shares INTEGER DEFAULT 0,
                posted_at TEXT,
                post_url TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS ahb_social_approval_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                actor TEXT,
                note TEXT,
                at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS ahb_social_post_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER NOT NULL,
                version_at TEXT DEFAULT CURRENT_TIMESTAMP,
                snapshot TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_hashtag_snapshots_tag ON ahb_social_hashtag_snapshots(tag);
            CREATE INDEX IF NOT EXISTS idx_post_versions_post ON ahb_social_post_versions(post_id);
            CREATE INDEX IF NOT EXISTS idx_approval_events_post ON ahb_social_approval_events(post_id);
        """)
        try:
            con.executescript("""
                CREATE VIRTUAL TABLE IF NOT EXISTS ahb_social_posts_fts USING fts5(
                    caption, hashtags, first_comment,
                    content='ahb_social_posts',
                    content_rowid='id'
                );
                CREATE TRIGGER IF NOT EXISTS ahb_social_posts_ai AFTER INSERT ON ahb_social_posts BEGIN
                    INSERT INTO ahb_social_posts_fts(rowid, caption, hashtags, first_comment)
                    VALUES (new.id, new.caption, new.hashtags, new.first_comment);
                END;
                CREATE TRIGGER IF NOT EXISTS ahb_social_posts_au AFTER UPDATE ON ahb_social_posts BEGIN
                    INSERT INTO ahb_social_posts_fts(ahb_social_posts_fts, rowid, caption, hashtags, first_comment)
                    VALUES('delete', old.id, old.caption, old.hashtags, old.first_comment);
                    INSERT INTO ahb_social_posts_fts(rowid, caption, hashtags, first_comment)
                    VALUES (new.id, new.caption, new.hashtags, new.first_comment);
                END;
                CREATE TRIGGER IF NOT EXISTS ahb_social_posts_ad AFTER DELETE ON ahb_social_posts BEGIN
                    INSERT INTO ahb_social_posts_fts(ahb_social_posts_fts, rowid, caption, hashtags, first_comment)
                    VALUES('delete', old.id, old.caption, old.hashtags, old.first_comment);
                END;
            """)
            con.execute("""
                INSERT INTO ahb_social_posts_fts(rowid, caption, hashtags, first_comment)
                SELECT id, caption, hashtags, first_comment FROM ahb_social_posts
                WHERE id NOT IN (SELECT rowid FROM ahb_social_posts_fts)
            """)
        except sqlite3.OperationalError as e:
            print(f"[startup] FTS5 unavailable, search will fall back to LIKE: {e}", flush=True)
        con.commit()
    except sqlite3.OperationalError as e:
        print(f"[startup] _ensure_social_v22_tables deferred: {e}", flush=True)
    finally:
        if con is not None:
            con.close()


_ensure_social_v22_tables()


try:
    from dashboard import social_workflow, social_trends, social_analytics
except ImportError:
    import social_workflow
    import social_trends
    import social_analytics
social_workflow.register(social_bp)
social_trends.register(social_bp)
social_analytics.register(social_bp)
