"""Social Studio v2.2 — inspo URL parse, hashtag snapshots, competitors, sounds, inspo library."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))

_ALLOWED_PLATFORMS = {"tiktok", "instagram", "youtube", "other"}
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_HASHTAG_RE = re.compile(r"#\w+")


def _db():
    path = os.environ.get("BAZA_DASHBOARD_DB",
                          os.path.join(_HERE, "baza_projects.db"))
    con = sqlite3.connect(path, timeout=8.0)
    con.row_factory = sqlite3.Row
    return con


def _inspo_dir() -> str:
    return os.environ.get(
        "BAZA_SOCIAL_INSPO_DIR",
        os.path.join(_HERE, "static", "social", "inspo"),
    )


def _days_ago(yyyymmdd: str):
    """Compute days between upload_date YYYYMMDD and now. None on parse fail."""
    if not yyyymmdd or len(yyyymmdd) != 8 or not yyyymmdd.isdigit():
        return None
    try:
        d = datetime.strptime(yyyymmdd, "%Y%m%d")
        delta = datetime.utcnow() - d
        return max(0, delta.days)
    except Exception:
        return None


def register(bp):
    from flask import jsonify, request

    # ---------------- 1. URL paste (inspo) ----------------

    @bp.route("/api/ahb/social/trends/inspo-url", methods=["POST"])
    def trends_inspo_url():
        data = request.get_json(silent=True) or {}
        url = (data.get("url") or "").strip()
        if not url or not _URL_RE.match(url):
            return jsonify({"error": "url must start with http:// or https://"}), 400
        try:
            proc = subprocess.run(
                ["yt-dlp", "--skip-download", "--print-json",
                 "--no-warnings", url],
                timeout=30, capture_output=True, text=True,
            )
        except FileNotFoundError:
            return jsonify({"error": "yt-dlp not installed"}), 502
        except subprocess.TimeoutExpired:
            return jsonify({"error": "yt-dlp timed out"}), 502
        except Exception as e:
            return jsonify({"error": f"yt-dlp failed: {e}"}), 502
        if proc.returncode != 0:
            return jsonify({
                "error": "yt-dlp failed",
                "stderr": (proc.stderr or "")[:500],
            }), 502
        try:
            meta = json.loads(proc.stdout.strip().split("\n")[0])
        except Exception as e:
            return jsonify({"error": f"could not parse yt-dlp JSON: {e}"}), 502
        description = meta.get("description") or ""
        hashtags = _HASHTAG_RE.findall(description)
        upload_date = meta.get("upload_date") or ""
        return jsonify({
            "title": meta.get("title") or "",
            "description": description,
            "hashtags": hashtags,
            "views": meta.get("view_count"),
            "uploader": meta.get("uploader") or "",
            "thumbnail_url": meta.get("thumbnail") or "",
            "days_ago": _days_ago(upload_date),
            "raw_uploaded": upload_date,
        })

    # ---------------- 2. Hashtag tracker ----------------

    @bp.route("/api/ahb/social/trends/hashtag-snapshots", methods=["POST"])
    def trends_hashtag_create():
        data = request.get_json(silent=True) or {}
        tag = (data.get("tag") or "").strip()
        if not tag:
            return jsonify({"error": "tag required"}), 400
        source_url = (data.get("source_url") or "").strip() or None
        notes = (data.get("notes") or "").strip() or None
        con = _db()
        try:
            cur = con.execute(
                "INSERT INTO ahb_social_hashtag_snapshots (tag, source_url, notes) "
                "VALUES (?, ?, ?)",
                (tag, source_url, notes),
            )
            con.commit()
            new_id = cur.lastrowid
        finally:
            con.close()
        return jsonify({"id": new_id})

    @bp.route("/api/ahb/social/trends/hashtag-snapshots", methods=["GET"])
    def trends_hashtag_list():
        con = _db()
        try:
            rows = con.execute(
                "SELECT id, tag, observed_at, source_url, notes "
                "FROM ahb_social_hashtag_snapshots "
                "ORDER BY observed_at DESC, id DESC"
            ).fetchall()
        finally:
            con.close()
        return jsonify({"items": [dict(r) for r in rows]})

    @bp.route("/api/ahb/social/trends/hashtag-snapshots/<tag>", methods=["GET"])
    def trends_hashtag_filter(tag):
        con = _db()
        try:
            rows = con.execute(
                "SELECT id, tag, observed_at, source_url, notes "
                "FROM ahb_social_hashtag_snapshots WHERE tag = ? "
                "ORDER BY observed_at DESC, id DESC",
                (tag,),
            ).fetchall()
        finally:
            con.close()
        return jsonify({"items": [dict(r) for r in rows]})

    # ---------------- 3. Competitor watch ----------------

    @bp.route("/api/ahb/social/trends/competitors", methods=["POST"])
    def trends_competitor_create():
        data = request.get_json(silent=True) or {}
        handle = (data.get("handle") or "").strip()
        platform = (data.get("platform") or "").strip().lower()
        notes = (data.get("notes") or "").strip() or None
        if not handle:
            return jsonify({"error": "handle required"}), 400
        if platform not in _ALLOWED_PLATFORMS:
            return jsonify({
                "error": f"platform must be one of {sorted(_ALLOWED_PLATFORMS)}"
            }), 400
        con = _db()
        try:
            cur = con.execute(
                "INSERT INTO ahb_social_competitors (handle, platform, notes) "
                "VALUES (?, ?, ?)",
                (handle, platform, notes),
            )
            con.commit()
            new_id = cur.lastrowid
        finally:
            con.close()
        return jsonify({"id": new_id})

    @bp.route("/api/ahb/social/trends/competitors", methods=["GET"])
    def trends_competitor_list():
        con = _db()
        try:
            rows = con.execute(
                "SELECT id, handle, platform, notes, created_at "
                "FROM ahb_social_competitors "
                "ORDER BY created_at DESC, id DESC"
            ).fetchall()
        finally:
            con.close()
        return jsonify({"items": [dict(r) for r in rows]})

    @bp.route("/api/ahb/social/trends/competitors/<int:cid>", methods=["DELETE"])
    def trends_competitor_delete(cid):
        con = _db()
        try:
            row = con.execute(
                "SELECT id FROM ahb_social_competitors WHERE id = ?",
                (cid,),
            ).fetchone()
            if not row:
                return jsonify({"error": "not found"}), 404
            con.execute("DELETE FROM ahb_social_competitors WHERE id = ?", (cid,))
            con.commit()
        finally:
            con.close()
        return jsonify({"ok": True})

    # ---------------- 4. Sound tracker ----------------

    @bp.route("/api/ahb/social/trends/sound-snapshots", methods=["POST"])
    def trends_sound_create():
        data = request.get_json(silent=True) or {}
        sound_url = (data.get("sound_url") or "").strip()
        if not sound_url:
            return jsonify({"error": "sound_url required"}), 400
        example_video_url = (data.get("example_video_url") or "").strip() or None
        title = (data.get("title") or "").strip() or None
        notes = (data.get("notes") or "").strip() or None
        con = _db()
        try:
            cur = con.execute(
                "INSERT INTO ahb_social_sound_snapshots "
                "(sound_url, example_video_url, title, notes) "
                "VALUES (?, ?, ?, ?)",
                (sound_url, example_video_url, title, notes),
            )
            con.commit()
            new_id = cur.lastrowid
        finally:
            con.close()
        return jsonify({"id": new_id})

    @bp.route("/api/ahb/social/trends/sound-snapshots", methods=["GET"])
    def trends_sound_list():
        con = _db()
        try:
            rows = con.execute(
                "SELECT id, sound_url, example_video_url, title, observed_at, notes "
                "FROM ahb_social_sound_snapshots "
                "ORDER BY observed_at DESC, id DESC"
            ).fetchall()
        finally:
            con.close()
        return jsonify({"items": [dict(r) for r in rows]})

    # ---------------- 5. Inspo library ----------------

    @bp.route("/api/ahb/social/trends/inspo-library", methods=["GET"])
    def trends_inspo_library():
        d = _inspo_dir()
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
        items = []
        if not os.path.isdir(d):
            return jsonify({"items": []})
        try:
            names = sorted(os.listdir(d))
        except Exception:
            return jsonify({"items": []})
        for name in names:
            if not name.endswith(".json"):
                continue
            path = os.path.join(d, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    obj = json.load(f)
            except Exception:
                continue
            items.append({
                "file_name": name,
                "category": obj.get("category", ""),
                "thumbnail": obj.get("thumbnail", ""),
                "caption": obj.get("caption", ""),
                "hook": obj.get("hook", ""),
                "structural_analysis": obj.get("structural_analysis", ""),
            })
        return jsonify({"items": items})
