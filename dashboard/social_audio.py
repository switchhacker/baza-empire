"""Social Studio v2.1 — audio pipeline.

- Music library indexer: scans dashboard/static/social/music/free/ at boot
  and via /api/ahb/social/music/reindex; extracts BPM/duration via librosa.
- Music search endpoint with filters.
- Voiceover + denoise/normalize/duck added in later tasks.
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
MUSIC_DIR = os.path.join(_HERE, "static", "social", "music", "free")


def _db():
    path = os.environ.get(
        "BAZA_DASHBOARD_DB",
        os.path.join(_HERE, "baza_projects.db"),
    )
    con = sqlite3.connect(path, timeout=8.0)
    con.row_factory = sqlite3.Row
    return con


def _probe_audio(path: str) -> dict:
    """Return {bpm, key_signature, duration_seconds, mood} for an audio file."""
    out = {"bpm": None, "key_signature": None, "duration_seconds": None, "mood": None}
    try:
        import librosa
        y, sr = librosa.load(path, sr=22050, mono=True)
        out["duration_seconds"] = float(len(y) / sr)
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        out["bpm"] = int(round(float(tempo)))
        b = out["bpm"]
        if b is None:
            out["mood"] = None
        elif b > 140:
            out["mood"] = "energetic"
        elif b > 90:
            out["mood"] = "moderate"
        else:
            out["mood"] = "calm"
        lower = os.path.basename(path).lower()
        for keyword, mood in [("chill", "calm"), ("epic", "energetic"),
                              ("trap", "energetic"), ("ambient", "calm"),
                              ("upbeat", "energetic"), ("sad", "calm")]:
            if keyword in lower:
                out["mood"] = mood
                break
    except Exception as e:
        print(f"[social_audio] librosa probe failed for {path}: {e}", flush=True)
    return out


def _index_music_dir(d: str) -> dict:
    if not os.path.isdir(d):
        return {"indexed": 0, "skipped": 0, "error": "directory missing"}
    indexed = 0
    skipped = 0
    con = _db()
    try:
        for fn in os.listdir(d):
            if not fn.lower().endswith((".mp3", ".wav", ".flac", ".ogg", ".m4a")):
                continue
            path = os.path.join(d, fn)
            exists = con.execute(
                "SELECT id FROM ahb_social_music_library WHERE path=?", (path,)
            ).fetchone()
            if exists:
                skipped += 1
                continue
            probe = _probe_audio(path)
            title = os.path.splitext(fn)[0].replace("_", " ").replace("-", " ").title()
            con.execute("""INSERT INTO ahb_social_music_library
                (path, title, bpm, key_signature, duration_seconds, mood)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (path, title, probe["bpm"], probe["key_signature"],
                 probe["duration_seconds"], probe["mood"]))
            indexed += 1
        con.commit()
    finally:
        con.close()
    return {"indexed": indexed, "skipped": skipped}


def register(bp):
    from flask import jsonify, request, send_file

    @bp.route("/api/ahb/social/music", methods=["GET"])
    def social_music_list():
        mood = request.args.get("mood")
        min_bpm = request.args.get("min_bpm", type=int)
        max_bpm = request.args.get("max_bpm", type=int)
        q = (request.args.get("q") or "").strip().lower()
        sql = "SELECT * FROM ahb_social_music_library WHERE 1=1"
        args = []
        if mood:
            sql += " AND mood=?"; args.append(mood)
        if min_bpm is not None:
            sql += " AND bpm>=?"; args.append(min_bpm)
        if max_bpm is not None:
            sql += " AND bpm<=?"; args.append(max_bpm)
        if q:
            sql += " AND LOWER(title) LIKE ?"; args.append(f"%{q}%")
        sql += " ORDER BY title LIMIT 200"
        con = _db()
        try:
            rows = con.execute(sql, args).fetchall()
        finally:
            con.close()
        return jsonify({"items": [dict(r) for r in rows]})

    @bp.route("/api/ahb/social/music/reindex", methods=["POST"])
    def social_music_reindex():
        result = _index_music_dir(MUSIC_DIR)
        return jsonify(result)

    @bp.route("/api/ahb/social/music/file/<int:mid>", methods=["GET"])
    def social_music_file(mid: int):
        con = _db()
        try:
            r = con.execute("SELECT path FROM ahb_social_music_library WHERE id=?", (mid,)).fetchone()
        finally:
            con.close()
        if not r or not os.path.exists(r["path"]):
            return jsonify({"error": "not found"}), 404
        return send_file(r["path"])


# Index at module import time (boot)
try:
    _index_music_dir(MUSIC_DIR)
except Exception as e:
    print(f"[social_audio] boot index failed: {e}", flush=True)
