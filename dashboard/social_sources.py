"""Social Studio v2.1 — source acquisition.

POST /api/ahb/social/sources/upload — multipart upload from webcam/screen
recorders and direct file pickers. Saves under
dashboard/uploads/social/<YYYY-MM-DD>/<uuid>.<ext>. WebM video is
transcoded to MP4 (libx264/aac) for downstream ffmpeg work. The new file
is inserted into image_captions so the source picker sees it.

Supported MIME types: image/png, image/jpeg, image/webp, image/heic,
                     video/mp4, video/quicktime, video/webm, audio/webm,
                     audio/wav, audio/mp3, audio/mpeg.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import threading
import time
import uuid
from datetime import datetime
from typing import Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
UPLOADS_DIR = os.path.join(_HERE, "uploads", "social")

# (extension, mime_prefix) tuples that we accept and how they classify.
_ACCEPTED = {
    ".png":  "image",
    ".jpg":  "image",
    ".jpeg": "image",
    ".webp": "image",
    ".heic": "image",
    ".mp4":  "video",
    ".mov":  "video",
    ".webm": "video",
    ".m4v":  "video",
    ".mkv":  "video",
    ".wav":  "audio",
    ".mp3":  "audio",
    ".m4a":  "audio",
    ".ogg":  "audio",
}

# Cap a single upload at 200MB (raw bytes); recorders typically max out around 50-80MB
MAX_UPLOAD_BYTES = 200 * 1024 * 1024

# URL-import rate limit (per-process): 5 imports per hour.
_URL_IMPORT_RATE_LOCK = threading.Lock()
_URL_IMPORT_TIMESTAMPS: list = []
URL_IMPORT_RATE_LIMIT = 5
URL_IMPORT_WINDOW_SECONDS = 3600


def _url_import_allowed() -> tuple:
    """Returns (allowed: bool, retry_after_seconds: int)."""
    now = time.time()
    with _URL_IMPORT_RATE_LOCK:
        # Drop timestamps older than the window
        cutoff = now - URL_IMPORT_WINDOW_SECONDS
        _URL_IMPORT_TIMESTAMPS[:] = [t for t in _URL_IMPORT_TIMESTAMPS if t > cutoff]
        if len(_URL_IMPORT_TIMESTAMPS) >= URL_IMPORT_RATE_LIMIT:
            oldest = _URL_IMPORT_TIMESTAMPS[0]
            retry_after = int(URL_IMPORT_WINDOW_SECONDS - (now - oldest))
            return False, max(retry_after, 1)
        _URL_IMPORT_TIMESTAMPS.append(now)
        return True, 0


def _db():
    path = os.environ.get(
        "BAZA_DASHBOARD_DB",
        os.path.join(_HERE, "baza_projects.db"),
    )
    con = sqlite3.connect(path, timeout=8.0)
    con.row_factory = sqlite3.Row
    return con


def _ensure_image_captions_table(con) -> None:
    """Best-effort: create image_captions if it doesn't exist. The dashboard's
    indexer normally owns this; we only add the columns we write to."""
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


def _transcode_webm_to_mp4(in_path: str, out_path: str) -> bool:
    """Best-effort transcode. Returns True on success."""
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", in_path,
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
             "-c:a", "aac", "-b:a", "128k",
             "-movflags", "+faststart",
             out_path],
            check=True, capture_output=True, timeout=180,
        )
        return os.path.exists(out_path) and os.path.getsize(out_path) > 0
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def register(bp):
    from flask import jsonify, request

    @bp.route("/api/ahb/social/sources/upload", methods=["POST"])
    def social_sources_upload():
        file = request.files.get("file")
        if not file:
            return jsonify({"error": "file required (multipart 'file')"}), 400
        original = file.filename or "upload"
        ext = os.path.splitext(original)[1].lower()
        if ext not in _ACCEPTED:
            return jsonify({
                "error": f"extension {ext or '(none)'} not accepted",
                "accepted": sorted(_ACCEPTED.keys()),
            }), 400
        kind = _ACCEPTED[ext]
        # Use ?source= to distinguish webcam/screen/direct/voicememo (informational)
        source = (request.args.get("source") or request.form.get("source") or "upload").lower()
        source = "".join(c for c in source if c.isalnum() or c in ("-", "_"))[:32] or "upload"
        # Save with a uuid name to dodge collisions and avoid leaking client filenames
        day = datetime.utcnow().strftime("%Y-%m-%d")
        day_dir = os.path.join(UPLOADS_DIR, day)
        os.makedirs(day_dir, exist_ok=True)
        uid = uuid.uuid4().hex[:12]
        dest_name = f"{uid}{ext}"
        dest = os.path.join(day_dir, dest_name)
        # Stream + cap
        total = 0
        with open(dest, "wb") as out_f:
            while True:
                chunk = file.stream.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    out_f.close()
                    try:
                        os.remove(dest)
                    except OSError:
                        pass
                    return jsonify({
                        "error": f"upload exceeds {MAX_UPLOAD_BYTES // (1024*1024)}MB",
                    }), 400
                out_f.write(chunk)
        # Transcode WebM video → MP4
        final_path = dest
        if ext == ".webm" and kind == "video":
            mp4_path = os.path.join(day_dir, f"{uid}.mp4")
            if _transcode_webm_to_mp4(dest, mp4_path):
                try:
                    os.remove(dest)
                except OSError:
                    pass
                final_path = mp4_path
        # Insert into image_captions so the source picker sees it
        con = _db()
        try:
            _ensure_image_captions_table(con)
            cur = con.execute(
                "INSERT INTO image_captions (project_id, sub_path, caption, tags, indexed_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (None, final_path, f"Recorded via {source}", source,
                 datetime.utcnow().isoformat(timespec="seconds")),
            )
            con.commit()
            new_id = cur.lastrowid
        finally:
            con.close()
        return jsonify({
            "ok": True,
            "id": new_id,
            "path": final_path,
            "kind": kind,
            "source": source,
            "size_bytes": total,
        })

    @bp.route("/api/ahb/social/sources/url-import", methods=["POST"])
    def social_sources_url_import():
        data = request.get_json(silent=True) or {}
        url = (data.get("url") or "").strip()
        if not url:
            return jsonify({"error": "url required"}), 400
        if not (url.startswith("http://") or url.startswith("https://")):
            return jsonify({"error": "url must start with http:// or https://"}), 400
        allowed, retry_after = _url_import_allowed()
        if not allowed:
            return jsonify({
                "error": "rate limit exceeded",
                "retry_after_seconds": retry_after,
                "limit": URL_IMPORT_RATE_LIMIT,
                "window_seconds": URL_IMPORT_WINDOW_SECONDS,
            }), 429
        try:
            import yt_dlp
        except ImportError:
            return jsonify({"error": "yt-dlp not installed"}), 500
        day = datetime.utcnow().strftime("%Y-%m-%d")
        day_dir = os.path.join(UPLOADS_DIR, day)
        os.makedirs(day_dir, exist_ok=True)
        uid = uuid.uuid4().hex[:12]
        out_tpl = os.path.join(day_dir, f"{uid}.%(ext)s")
        ydl_opts = {
            "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
            "outtmpl": out_tpl,
            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 30,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
        except yt_dlp.utils.DownloadError as e:
            return jsonify({"error": "download failed", "detail": str(e)[-200:]}), 400
        except Exception as e:
            return jsonify({"error": "yt-dlp error", "detail": str(e)[-200:]}), 500
        # Locate the produced file (may have any of mp4/webm/mkv extensions)
        produced = None
        for ext in (".mp4", ".mkv", ".webm", ".mov"):
            cand = os.path.join(day_dir, f"{uid}{ext}")
            if os.path.exists(cand):
                produced = cand
                break
        if not produced:
            return jsonify({"error": "download succeeded but file not located"}), 500
        title = (info.get("title") or "Imported video").strip()
        con = _db()
        try:
            _ensure_image_captions_table(con)
            cur = con.execute(
                "INSERT INTO image_captions (project_id, sub_path, caption, tags, indexed_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (None, produced, title, "url-import",
                 datetime.utcnow().isoformat(timespec="seconds")),
            )
            con.commit()
            new_id = cur.lastrowid
        finally:
            con.close()
        return jsonify({
            "ok": True,
            "id": new_id,
            "path": produced,
            "title": title,
            "duration": info.get("duration"),
            "source": "url-import",
        })

    @bp.route("/api/ahb/social/sources/voice-memo", methods=["POST"])
    def social_sources_voice_memo():
        file = request.files.get("file")
        if not file:
            return jsonify({"error": "file required (multipart 'file')"}), 400
        original = file.filename or "memo.webm"
        ext = os.path.splitext(original)[1].lower() or ".webm"
        if ext not in (".webm", ".wav", ".mp3", ".m4a", ".ogg"):
            return jsonify({"error": "audio extension required", "got": ext}), 400
        day = datetime.utcnow().strftime("%Y-%m-%d")
        day_dir = os.path.join(UPLOADS_DIR, day)
        os.makedirs(day_dir, exist_ok=True)
        uid = uuid.uuid4().hex[:12]
        dest = os.path.join(day_dir, f"voice_{uid}{ext}")
        total = 0
        with open(dest, "wb") as out_f:
            while True:
                chunk = file.stream.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    out_f.close()
                    try:
                        os.remove(dest)
                    except OSError:
                        pass
                    return jsonify({
                        "error": f"upload exceeds {MAX_UPLOAD_BYTES // (1024*1024)}MB",
                    }), 400
                out_f.write(chunk)
        # Transcribe with faster_whisper (reuses social_audio's cached model)
        transcript = ""
        try:
            try:
                from dashboard import social_audio
            except ImportError:
                import social_audio
            model = social_audio._get_whisper()
            # Whisper handles webm/opus directly via ffmpeg under the hood
            segments, _info = model.transcribe(dest, beam_size=1)
            transcript = " ".join(seg.text.strip() for seg in segments).strip()
        except Exception as e:
            transcript = ""
            transcribe_error = str(e)[-200:]
        else:
            transcribe_error = None
        # Insert as a source row (audio kind)
        con = _db()
        try:
            _ensure_image_captions_table(con)
            cur = con.execute(
                "INSERT INTO image_captions (project_id, sub_path, caption, tags, indexed_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (None, dest, transcript or "Voice memo", "voice-memo",
                 datetime.utcnow().isoformat(timespec="seconds")),
            )
            con.commit()
            new_id = cur.lastrowid
        finally:
            con.close()
        return jsonify({
            "ok": True,
            "id": new_id,
            "path": dest,
            "transcript": transcript,
            "transcribe_error": transcribe_error,
            "size_bytes": total,
        })
