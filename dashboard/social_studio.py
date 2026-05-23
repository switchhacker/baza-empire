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
    q = (request.args.get("q") or "").strip().lower()
    limit = min(request.args.get("limit", default=100, type=int), 500)
    offset = max(0, request.args.get("offset", default=0, type=int))
    if status and status not in ALLOWED_STATUSES:
        return jsonify({"error": "invalid status"}), 400
    if platform and platform not in ALLOWED_PLATFORMS:
        return jsonify({"error": "invalid platform"}), 400
    sql = "SELECT * FROM ahb_social_posts WHERE 1=1"
    args = []
    if status:
        sql += " AND status=?"; args.append(status)
    if platform:
        sql += " AND platform=?"; args.append(platform)
    if project_id is not None:
        sql += " AND project_id=?"; args.append(project_id)
    if q:
        sql += " AND (LOWER(caption) LIKE ? OR LOWER(hashtags) LIKE ?)"
        args += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    con = _conn()
    try:
        rows = con.execute(sql, args).fetchall()
    finally:
        con.close()
    return jsonify({"items": [_row_to_post(r) for r in rows]})


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
        con.execute(f"UPDATE ahb_social_posts SET {','.join(sets)} WHERE id=?", vals)
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


def _resolve_media_paths(source_media_ids: list) -> list:
    """Map image_captions.id → absolute file path. Joins sub_path under the
    baza cloud root if not absolute."""
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
    cloud_root = os.environ.get(
        "BAZA_CLOUD_ROOT",
        "/home/switchhacker/baza-cloud",
    )
    cloud_root_abs = os.path.abspath(cloud_root)
    paths = []
    for r in rows:
        p = r["sub_path"]
        if not os.path.isabs(p):
            p = os.path.join(cloud_root, p)
        p_abs = os.path.abspath(p)
        # Reject if path escapes the cloud root (path traversal defense)
        if not p_abs.startswith(cloud_root_abs + os.sep) and p_abs != cloud_root_abs:
            continue
        if os.path.exists(p_abs):
            paths.append(p_abs)
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
    paths = _resolve_media_paths(post["source_media_ids"])
    if not paths:
        return jsonify({"error": "no resolvable source media"}), 400
    out_dir = os.path.join(
        DASHBOARD_DIR, "artifacts", "social",
        datetime.utcnow().strftime("%Y-%m-%d"), str(pid),
    )
    os.makedirs(out_dir, exist_ok=True)
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
    if not r or not r["cover_path"] or not os.path.exists(r["cover_path"]):
        return jsonify({"error": "no cover"}), 404
    return send_file(r["cover_path"])


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
            paths = _resolve_media_paths(source_ids)
            if not paths:
                _job_finish(job_id, "failed", error="no resolvable source media")
                return
            out_dir = os.path.join(
                DASHBOARD_DIR, "artifacts", "social",
                datetime.utcnow().strftime("%Y-%m-%d"), str(post_id),
            )
            os.makedirs(out_dir, exist_ok=True)
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


@social_bp.route("/api/ahb/social/autopilot/tick", methods=["POST"])
def autopilot_tick():
    s = _settings.load_settings()
    if not s.get("autopilot_master"):
        return jsonify({"ran": 0, "reason": "master off"})
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
    return jsonify({"ran": len(ran), "post_ids": ran})


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


@social_bp.route("/api/ahb/social/posts/<int:pid>/telegram", methods=["POST"])
def social_post_telegram(pid: int):
    """Drop the bundle (or caption + cover) to Serge's Telegram via the
    Specter bridge /notify endpoint."""
    con = _conn()
    try:
        r = con.execute("SELECT * FROM ahb_social_posts WHERE id=?", (pid,)).fetchone()
    finally:
        con.close()
    if not r:
        return jsonify({"error": "not found"}), 404
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
        return jsonify({"error": f"bridge unavailable: {e}"}), 502
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
