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
PIPER_VOICES_DIR = os.path.join(_HERE, "static", "social", "piper-voices")
VOICEOVER_OUT_DIR = os.path.join(_HERE, "artifacts", "social", "voiceover")


def _piper_bin() -> Optional[str]:
    """Locate the piper binary: PATH first, then venv-relative, then sys.executable dir."""
    import shutil
    import sys

    found = shutil.which("piper")
    if found:
        return found
    candidates = [
        os.path.join(os.path.dirname(sys.executable), "piper"),
        os.path.join(os.path.dirname(os.path.dirname(_HERE)), "venv", "bin", "piper"),
    ]
    for c in candidates:
        if os.path.exists(c) and os.access(c, os.X_OK):
            return c
    return None

# Lazy-loaded faster_whisper model (process-cached)
_WHISPER_MODEL = None


def _srt_ts(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _get_whisper():
    global _WHISPER_MODEL
    if _WHISPER_MODEL is None:
        from faster_whisper import WhisperModel
        _WHISPER_MODEL = WhisperModel("tiny.en", device="cpu", compute_type="int8")
    return _WHISPER_MODEL


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

    @bp.route("/api/ahb/social/posts/<int:pid>/subtitles", methods=["POST"])
    def social_subtitles_generate(pid: int):
        import subprocess
        import tempfile

        con = _db()
        try:
            row = con.execute(
                "SELECT asset_path FROM ahb_social_posts WHERE id=?", (pid,)
            ).fetchone()
        finally:
            con.close()
        if not row or not row["asset_path"] or not os.path.exists(row["asset_path"]):
            return jsonify({"error": "post has no rendered asset"}), 400
        asset_path = row["asset_path"]
        try:
            model = _get_whisper()
        except Exception as e:
            return jsonify({"error": "whisper load failed", "detail": str(e)[-200:]}), 500
        wav = tempfile.mktemp(suffix=".wav")
        try:
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", asset_path, "-ar", "16000", "-ac", "1", wav],
                    check=True, capture_output=True,
                )
            except subprocess.CalledProcessError as e:
                return jsonify({
                    "error": "audio extract failed",
                    "detail": e.stderr.decode(errors="ignore")[-200:],
                }), 500
            try:
                segments, _info = model.transcribe(wav, beam_size=1)
                srt_path = os.path.splitext(asset_path)[0] + ".srt"
                with open(srt_path, "w") as f:
                    for i, seg in enumerate(segments, 1):
                        f.write(f"{i}\n")
                        f.write(f"{_srt_ts(seg.start)} --> {_srt_ts(seg.end)}\n")
                        f.write(f"{seg.text.strip()}\n\n")
            except Exception as e:
                return jsonify({"error": "transcribe failed", "detail": str(e)[-200:]}), 500
        finally:
            if os.path.exists(wav):
                try:
                    os.remove(wav)
                except OSError:
                    pass
        con = _db()
        try:
            con.execute(
                "UPDATE ahb_social_posts SET subtitles_path=? WHERE id=?",
                (srt_path, pid),
            )
            con.commit()
        finally:
            con.close()
        return jsonify({"ok": True, "subtitles_path": srt_path})

    @bp.route("/api/ahb/social/ai/voiceover", methods=["POST"])
    def social_voiceover():
        import subprocess as sp
        import tempfile

        data = request.get_json(silent=True) or {}
        text = (data.get("text") or "").strip()
        if not text:
            return jsonify({"error": "text required"}), 400
        voice = data.get("voice") or "en_US-amy-medium"
        # Reject path-traversal in voice name
        if "/" in voice or ".." in voice or "\\" in voice:
            return jsonify({"error": "invalid voice name"}), 400
        voice_path = os.path.join(PIPER_VOICES_DIR, f"{voice}.onnx")
        if not os.path.exists(voice_path):
            return jsonify({"error": f"voice not installed: {voice}"}), 400
        piper = _piper_bin()
        if not piper:
            return jsonify({
                "error": "piper not installed (run dashboard/social_install_assets.sh)",
            }), 500
        os.makedirs(VOICEOVER_OUT_DIR, exist_ok=True)
        out_path = tempfile.mktemp(suffix=".wav", dir=VOICEOVER_OUT_DIR)
        try:
            sp.run(
                [piper, "--model", voice_path, "--output_file", out_path],
                input=text.encode("utf-8"),
                check=True, capture_output=True,
            )
        except sp.CalledProcessError as e:
            return jsonify({
                "error": "piper failed",
                "detail": e.stderr.decode(errors="ignore")[-200:],
            }), 500
        except FileNotFoundError:
            return jsonify({
                "error": "piper not installed (run dashboard/social_install_assets.sh)",
            }), 500
        post_id = request.args.get("post_id", type=int)
        if post_id:
            con = _db()
            try:
                con.execute(
                    "UPDATE ahb_social_posts SET voiceover_path=? WHERE id=?",
                    (out_path, post_id),
                )
                con.commit()
            finally:
                con.close()
        return jsonify({
            "ok": True,
            "voiceover_path": out_path,
            "url": f"/api/ahb/social/ai/voiceover/preview?path={out_path}",
        })

    @bp.route("/api/ahb/social/ai/voiceover/preview", methods=["GET"])
    def social_voiceover_preview():
        path = request.args.get("path") or ""
        # Normalize and validate path is inside VOICEOVER_OUT_DIR (no traversal)
        try:
            real = os.path.realpath(path)
            root = os.path.realpath(VOICEOVER_OUT_DIR)
        except Exception:
            return jsonify({"error": "invalid"}), 400
        if not real.startswith(root + os.sep) or not os.path.exists(real):
            return jsonify({"error": "invalid"}), 400
        return send_file(real)


# Index at module import time (boot)
try:
    _index_music_dir(MUSIC_DIR)
except Exception as e:
    print(f"[social_audio] boot index failed: {e}", flush=True)
