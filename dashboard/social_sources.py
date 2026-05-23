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
