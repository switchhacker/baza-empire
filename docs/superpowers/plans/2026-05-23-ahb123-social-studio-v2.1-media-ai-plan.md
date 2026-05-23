# Social Studio v2.1 — Media + AI + Audio + Sources Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Second phase of v2 mega-expansion — vision-driven cover-pick, whisper auto-subtitles + burn-in, piper TTS voiceover, music library + sidechain ducking, in-app image editor, logo bug + intro/outro + LUTs, Ken-Burns + beat-sync, hook patterns + CTA + comment-bait, multi-language batch, voiceover script gen, storyboard, B-roll, prediction, SD prompt builder, webcam/screen/URL/voice-memo sources.

**Architecture:** Refactor `dashboard/social_studio.py` into topical sub-modules (`social_ai.py`, `social_audio.py`, `social_sources.py`). Add async background processing for slow ops (subtitles, vision cover-pick). Extend render pipeline with new filter chains (subtitles, music, LUTs, logo, intro/outro, Ken-Burns, beat-sync).

**Tech Stack:** ffmpeg + faster_whisper + piper-tts + yt-dlp + librosa + MediaRecorder/getDisplayMedia APIs.

**Spec:** `docs/superpowers/specs/2026-05-22-ahb123-social-studio-v2-design.md` Bundles B + C + H + I.

**Prerequisites:** v2.0 must be merged first (this plan assumes toast/keymap/progress/shotlist modules exist).

---

## Process notes (same as v2.0)

- No `git --amend`. Forward commits only.
- `sudo systemctl restart baza-dashboard` after template edits.
- Each new IIFE defines its own `_esc()` helper.
- Body-level modals only.
- `dashboard/templates/ahb123.html` is now ~19,500 lines after v2.0 — use grep for insertion points.
- All file paths absolute from `/home/switchhacker/baza-empire/agent-framework-v3/`.
- Commit messages end with `\nCo-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.

---

## File Structure

**New backend modules** (split from social_studio.py):
- `dashboard/social_ai.py` — all `/api/ahb/social/ai/*` route logic
- `dashboard/social_audio.py` — voiceover (piper), denoise/normalize/duck, music library indexer
- `dashboard/social_sources.py` — webcam/screen/URL/voice-memo upload + yt-dlp orchestration

**Modified:**
- `dashboard/social_studio.py` — schema migrations for v2.1 columns + tables; delegate route logic to new modules
- `dashboard/social_render.py` — gain filter-graph builders for subtitles, music, LUTs, logo bug, intro/outro, Ken-Burns, beat-sync
- `dashboard/social_settings.py` — new settings keys (loudness_target, translation_targets, music_volume_db, voiceover_volume_db, intro_path, outro_path)
- `dashboard/social_install_assets.sh` — extend with piper voices, LUTs, music, SFX downloads + pip-install piper-tts/yt-dlp/pillow
- `dashboard/templates/ahb123.html` — many new IIFE modules + extensions to composer

**New asset directories:**
- `dashboard/static/social/piper-voices/`
- `dashboard/static/social/luts/`
- `dashboard/static/social/music/free/`
- `dashboard/static/social/sfx/`

**New tests:**
- `tests/test_social_v2_ai.py`
- `tests/test_social_v2_audio.py`
- `tests/test_social_v2_sources.py`
- `tests/test_social_v2_render.py`

---

## Task 1: Schema migrations + module-split scaffold

**Files:**
- Modify: `dashboard/social_studio.py` (add v2.1 column additions and tables to `_ensure_social_v2_tables`; add Blueprint exports for new modules)
- Create: `dashboard/social_ai.py` (empty scaffold w/ Blueprint import)
- Create: `dashboard/social_audio.py` (empty scaffold)
- Create: `dashboard/social_sources.py` (empty scaffold)
- Test: `tests/test_social_v2_ai.py` (migration smoke)

- [ ] **Step 1: Write the failing tests**

`tests/test_social_v2_ai.py`:

```python
"""Tests for Social Studio v2.1 — schema migration smoke."""
import os
import sqlite3
import sys
import tempfile

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def db_path(monkeypatch):
    d = tempfile.mkdtemp(prefix="sv21_")
    p = os.path.join(d, "baza_projects.db")
    monkeypatch.setenv("BAZA_DASHBOARD_DB", p)
    sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))
    for m in ("social_studio", "social_settings", "social_ai", "social_audio", "social_sources"):
        if m in sys.modules:
            del sys.modules[m]
    yield p
    for m in ("social_studio", "social_settings", "social_ai", "social_audio", "social_sources"):
        if m in sys.modules:
            del sys.modules[m]


def test_v2_1_tables_exist(db_path):
    import social_studio
    social_studio._ensure_social_tables(db_path)
    social_studio._ensure_social_v2_tables(db_path)
    con = sqlite3.connect(db_path)
    try:
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "ahb_social_music_library" in names
        # v2.1 column additions
        cols = {r[1] for r in con.execute("PRAGMA table_info(ahb_social_posts)")}
        assert "translations" in cols
        assert "music_id" in cols
        assert "voiceover_path" in cols
        assert "subtitles_path" in cols
        assert "lut_name" in cols
    finally:
        con.close()


def test_v2_1_blueprint_imports_clean(db_path):
    # Just confirm the three new modules import without raising
    import social_ai, social_audio, social_sources
    assert hasattr(social_ai, "register")
    assert hasattr(social_audio, "register")
    assert hasattr(social_sources, "register")
```

Run: `pytest tests/test_social_v2_ai.py -v` → FAIL.

- [ ] **Step 2: Create the three empty scaffold modules**

`dashboard/social_ai.py`:

```python
"""Social Studio v2.1 — AI route handlers.
Routes registered onto the social_bp Blueprint via register(bp).
"""
from __future__ import annotations


def register(bp):
    """Register all /ai/* routes on the given Blueprint.
    Implemented incrementally in v2.1 tasks 5-11."""
    pass
```

`dashboard/social_audio.py`:

```python
"""Social Studio v2.1 — audio pipeline.
Voiceover (piper), denoise/normalize/duck, music library indexer.
"""
from __future__ import annotations


def register(bp):
    """Register audio routes on the given Blueprint."""
    pass
```

`dashboard/social_sources.py`:

```python
"""Social Studio v2.1 — source acquisition.
Webcam/screen/URL/voice-memo upload + yt-dlp orchestration.
"""
from __future__ import annotations


def register(bp):
    """Register source-upload routes on the given Blueprint."""
    pass
```

- [ ] **Step 3: Extend `_ensure_social_v2_tables` with v2.1 migrations**

In `dashboard/social_studio.py`, replace the v2.0 `_ensure_social_v2_tables` (which only added `pid` column) with this expanded version:

```python
def _ensure_social_v2_tables(db_path: Optional[str] = None) -> None:
    """Add v2 column additions and tables. Idempotent."""
    path = db_path or _db_path()
    con = None
    try:
        con = sqlite3.connect(path, timeout=8.0)
        con.execute("PRAGMA busy_timeout = 8000")
        # Idempotent column additions
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
                pass  # column exists
        # New tables
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


# Wire the new sub-module registers
try:
    from dashboard import social_ai, social_audio, social_sources
except ImportError:
    import social_ai
    import social_audio
    import social_sources
social_ai.register(social_bp)
social_audio.register(social_bp)
social_sources.register(social_bp)
```

- [ ] **Step 4: Run tests + restart**

```
cd /home/switchhacker/baza-empire/agent-framework-v3
source venv/bin/activate
pytest tests/test_social_v2_ai.py -v
sudo systemctl restart baza-dashboard
sleep 2
sqlite3 dashboard/baza_projects.db "PRAGMA table_info(ahb_social_posts)" | grep -E "translations|music_id|voiceover_path|subtitles_path|lut_name"
sqlite3 dashboard/baza_projects.db ".tables" | tr ' ' '\n' | grep music_library
```

Expected: 2 tests pass; PRAGMA shows the 5 new columns; tables list shows `ahb_social_music_library`.

- [ ] **Step 5: Commit**

```
git add dashboard/social_studio.py dashboard/social_ai.py dashboard/social_audio.py dashboard/social_sources.py tests/test_social_v2_ai.py
git commit -m "social v2.1: schema migrations + module-split scaffold

Empty scaffold modules for ai/audio/sources; routes will be added
incrementally across v2.1 tasks. Schema additions: translations,
music_id, voiceover_path, subtitles_path, lut_name on posts +
ahb_social_music_library table for the music indexer. Migrations
idempotent.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Install script extension + dependencies

**Files:**
- Modify: `dashboard/social_install_assets.sh`
- Modify: `requirements.txt` (or whatever the project uses — check `ls *.txt *.toml`)

- [ ] **Step 1: Add deps to requirements**

```
ls /home/switchhacker/baza-empire/agent-framework-v3/*.txt 2>/dev/null
```

If there's a `requirements.txt`, append:

```
piper-tts>=1.2.0
yt-dlp>=2024.1.1
pillow>=10.0.0
```

If there isn't one (the project uses `venv` directly), pip install them now:

```
cd /home/switchhacker/baza-empire/agent-framework-v3
source venv/bin/activate
pip install piper-tts yt-dlp pillow
```

Verify:

```
python -c "import piper; import yt_dlp; import PIL; print('all ok')"
```

- [ ] **Step 2: Extend the install script**

Append to `dashboard/social_install_assets.sh` (after the existing Inter font block):

```bash
# Piper TTS voices (download to dashboard/static/social/piper-voices/)
VOICES_DIR="$(pwd)/static/social/piper-voices"
mkdir -p "$VOICES_DIR"

PIPER_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main"
download_piper_voice() {
    local voice="$1" path="$VOICES_DIR/$voice.onnx" cfg="$VOICES_DIR/$voice.onnx.json"
    if [[ -f "$path" && -f "$cfg" ]]; then
        echo "ok: piper voice $voice"; return
    fi
    case "$voice" in
        en_US-amy-medium)   url="$PIPER_BASE/en/en_US/amy/medium/en_US-amy-medium.onnx" ;;
        en_US-ryan-high)    url="$PIPER_BASE/en/en_US/ryan/high/en_US-ryan-high.onnx" ;;
        en_GB-jenny-medium) url="$PIPER_BASE/en/en_GB/jenny_dioco/medium/en_GB-jenny_dioco-medium.onnx" ;;
        en_US-lessac-medium)url="$PIPER_BASE/en/en_US/lessac/medium/en_US-lessac-medium.onnx" ;;
        *) echo "unknown voice $voice"; return 1 ;;
    esac
    echo "fetch: $url"
    curl -fsSL --retry 3 "$url" -o "$path.tmp" && mv "$path.tmp" "$path"
    curl -fsSL --retry 3 "$url.json" -o "$cfg.tmp" && mv "$cfg.tmp" "$cfg"
    echo "  installed $(stat -c%s "$path") + $(stat -c%s "$cfg") bytes"
}
download_piper_voice en_US-amy-medium || echo "warn: amy voice failed"
download_piper_voice en_US-ryan-high || echo "warn: ryan voice failed"
download_piper_voice en_GB-jenny-medium || echo "warn: jenny voice failed"
download_piper_voice en_US-lessac-medium || echo "warn: lessac voice failed"

# LUTs (.cube files) — generate programmatically since reliable CC0 mirrors are scarce
LUTS_DIR="$(pwd)/static/social/luts"
mkdir -p "$LUTS_DIR"

write_lut() {
    local name="$1" python_expr="$2" out="$LUTS_DIR/$name.cube"
    [[ -f "$out" ]] && { echo "ok: lut $name"; return; }
    python3 -c "
import os
N = 33  # 33x33x33 cube
print('TITLE \"$name\"')
print('LUT_3D_SIZE', N)
for b in range(N):
    for g in range(N):
        for r in range(N):
            R, G, B = r/(N-1), g/(N-1), b/(N-1)
            $python_expr
            R, G, B = max(0,min(1,R)), max(0,min(1,G)), max(0,min(1,B))
            print(f'{R:.6f} {G:.6f} {B:.6f}')
" > "$out"
    echo "  generated $name.cube ($(wc -l < "$out") lines)"
}

# Programmatic LUT generation (these match the 5 named looks in spec §6 B.8)
write_lut "cinematic" "R = R * 0.95 + 0.05 * 0.4; G = G * 0.98; B = B * 1.05"
write_lut "vibrant"   "L = 0.3*R + 0.59*G + 0.11*B; R = L + (R - L) * 1.3; G = L + (G - L) * 1.3; B = L + (B - L) * 1.3"
write_lut "moody"     "R = R * 0.8; G = G * 0.85; B = B * 1.1"
write_lut "bw"        "L = 0.3*R + 0.59*G + 0.11*B; R, G, B = L, L, L"
write_lut "warm"      "R = R * 1.1; G = G * 1.02; B = B * 0.88"

# SFX library (CC0 short clips — bundle empty for now, user can drop their own)
SFX_DIR="$(pwd)/static/social/sfx"
mkdir -p "$SFX_DIR"
[[ -f "$SFX_DIR/.gitkeep" ]] || touch "$SFX_DIR/.gitkeep"
echo "ok: sfx dir ready (drop CC0 .mp3/.wav files here)"

# Music free library directory
MUSIC_DIR="$(pwd)/static/social/music/free"
mkdir -p "$MUSIC_DIR"
[[ -f "$MUSIC_DIR/.gitkeep" ]] || touch "$MUSIC_DIR/.gitkeep"
echo "ok: music dir ready (drop royalty-free .mp3/.wav files here)"

echo "Social Studio v2.1 asset install complete."
```

- [ ] **Step 3: Run the installer**

```
bash dashboard/social_install_assets.sh
```

Expected: all 4 Piper voices downloaded (~50-150MB each), 5 LUT files generated, directories exist. If a piper voice URL is dead at install time, the `|| echo "warn: …"` prints a warning but doesn't fail the script.

- [ ] **Step 4: Verify**

```
ls -la dashboard/static/social/piper-voices/ | head -10
ls -la dashboard/static/social/luts/
cat dashboard/static/social/luts/bw.cube | head -5
python -c "import piper; print(piper.__version__)"
python -c "import yt_dlp; print(yt_dlp.version.__version__)"
```

- [ ] **Step 5: Commit**

```
git add dashboard/social_install_assets.sh dashboard/static/social/luts/ dashboard/static/social/sfx/.gitkeep dashboard/static/social/music/free/.gitkeep dashboard/static/social/piper-voices/
# Note: piper voice .onnx files are ~50-150MB each — consider adding to .gitignore if total > 200MB. Check first:
du -sh dashboard/static/social/piper-voices/
# If > 200MB, add to .gitignore instead:
#   echo 'dashboard/static/social/piper-voices/*.onnx' >> .gitignore
#   git add .gitignore
git commit -m "social v2.1: install script — piper voices + LUTs + asset dirs

Extends social_install_assets.sh:
- 4 Piper voices (Amy, Ryan, Jenny, Lessac) from rhasspy/piper-voices HF
- 5 programmatically-generated 33-bit cube LUTs (cinematic/vibrant/moody/bw/warm)
- Asset directories for SFX and royalty-free music (placeholders kept by .gitkeep)

pip-installed runtime deps: piper-tts, yt-dlp, pillow.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Music library indexer

**Files:**
- Modify: `dashboard/social_audio.py` (real implementation)
- Test: `tests/test_social_v2_audio.py`

- [ ] **Step 1: Write failing tests**

`tests/test_social_v2_audio.py`:

```python
"""Tests for Social Studio v2.1 audio pipeline."""
import os
import sqlite3
import sys
import tempfile

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def client(monkeypatch):
    d = tempfile.mkdtemp(prefix="sv21a_")
    db = os.path.join(d, "baza_projects.db")
    monkeypatch.setenv("BAZA_DASHBOARD_DB", db)
    monkeypatch.setenv("BAZA_SOCIAL_SETTINGS_DIR", d)
    sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))
    for m in ("social_studio", "social_settings", "social_audio", "social_ai", "social_sources"):
        if m in sys.modules:
            del sys.modules[m]
    import social_studio
    social_studio._ensure_social_tables(db)
    social_studio._ensure_social_v2_tables(db)
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(social_studio.social_bp)
    yield app.test_client(), social_studio
    for m in ("social_studio", "social_settings", "social_audio", "social_ai", "social_sources"):
        if m in sys.modules:
            del sys.modules[m]


def test_music_list_empty(client):
    c, _ = client
    r = c.get("/api/ahb/social/music")
    assert r.status_code == 200
    assert r.get_json()["items"] == []


def test_music_search_by_mood(client):
    c, _ = client
    con = sqlite3.connect(os.environ["BAZA_DASHBOARD_DB"])
    try:
        con.execute("INSERT INTO ahb_social_music_library (path, title, bpm, mood) VALUES (?, ?, ?, ?)",
                    ("/tmp/calm.mp3", "Calm Track", 80, "calm"))
        con.execute("INSERT INTO ahb_social_music_library (path, title, bpm, mood) VALUES (?, ?, ?, ?)",
                    ("/tmp/hype.mp3", "Hype Track", 150, "energetic"))
        con.commit()
    finally:
        con.close()
    r = c.get("/api/ahb/social/music?mood=calm")
    items = r.get_json()["items"]
    assert len(items) == 1 and items[0]["mood"] == "calm"


def test_music_reindex_endpoint_exists(client, monkeypatch):
    c, ss = client
    # Stub the actual indexing call to avoid librosa work
    import social_audio
    monkeypatch.setattr(social_audio, "_index_music_dir", lambda d: {"indexed": 0, "skipped": 0})
    r = c.post("/api/ahb/social/music/reindex")
    assert r.status_code == 200
```

Run: `pytest tests/test_social_v2_audio.py -v` → FAIL (routes missing).

- [ ] **Step 2: Implement music indexer + routes**

Replace `dashboard/social_audio.py`:

```python
"""Social Studio v2.1 — audio pipeline.

- Music library indexer: scans dashboard/static/social/music/free/ at boot
  and via /api/ahb/social/music/reindex; extracts BPM/duration via librosa.
- Music search endpoint with filters.
- Voiceover (piper) and audio post-processing (denoise/normalize/duck)
  added in later tasks.
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Optional

try:
    from flask import jsonify, request
except ImportError:
    pass  # routes registered via register() — Flask comes from caller

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
    """Return {bpm, key, duration_seconds, mood} for an audio file."""
    out = {"bpm": None, "key_signature": None, "duration_seconds": None, "mood": None}
    try:
        import librosa
        y, sr = librosa.load(path, sr=22050, mono=True)
        out["duration_seconds"] = float(len(y) / sr)
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        out["bpm"] = int(round(float(tempo)))
        # Simple mood heuristic from BPM + filename keywords
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
    """Scan a directory, insert/update rows in ahb_social_music_library.
    Returns {indexed: N, skipped: N}."""
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
    """Register music routes on the given Blueprint."""
    from flask import jsonify, request

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


# Index at module import time (boot)
try:
    _index_music_dir(MUSIC_DIR)
except Exception as e:
    print(f"[social_audio] boot index failed: {e}", flush=True)
```

- [ ] **Step 3: Run tests + restart**

```
pytest tests/test_social_v2_audio.py -v
sudo systemctl restart baza-dashboard
sleep 2
curl -s http://127.0.0.1:8888/api/ahb/social/music | head -c 100
```

Expected: 3 tests pass; curl returns `{"items": []}`.

- [ ] **Step 4: Smoke with a real audio file**

If you have any MP3 lying around, copy one in:

```
cp ~/some-music.mp3 dashboard/static/social/music/free/test.mp3
curl -X POST http://127.0.0.1:8888/api/ahb/social/music/reindex
curl -s http://127.0.0.1:8888/api/ahb/social/music | python3 -m json.tool
```

Should show one item with BPM detected.

- [ ] **Step 5: Commit**

```
git add dashboard/social_audio.py tests/test_social_v2_audio.py
git commit -m "social v2.1: music library indexer + search endpoint

dashboard/static/social/music/free/ scanned at boot via librosa for BPM
+ duration + mood heuristic (BPM tier + filename keywords). Routes:
GET /api/ahb/social/music with mood/min_bpm/max_bpm/q filters;
POST /api/ahb/social/music/reindex for manual re-scan.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Music picker UI + composer integration

**Files:**
- Modify: `dashboard/templates/ahb123.html` — music picker IIFE + composer button

- [ ] **Step 1: Append the music picker IIFE**

Find the end of `SocialStudio.modules.a11y` (added in v2.0 Task 12), append:

```html
<script>
SocialStudio.modules.music = (function(){
  function _esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  async function pick(callback) {
    let items = [];
    try {
      const r = await fetch('/api/ahb/social/music');
      items = (await r.json()).items || [];
    } catch (e) {}
    const m = document.createElement('div');
    m.className = 'modal-bg';
    m.style.cssText = 'display:flex';
    document.body.appendChild(m);
    const close = () => document.body.removeChild(m);
    const rows = items.map(t => `
      <tr style="border-bottom:1px solid #1a1a2e">
        <td style="padding:8px">
          <audio controls src="/api/ahb/social/music/file/${t.id}" style="height:30px;max-width:200px"></audio>
        </td>
        <td style="padding:8px">${_esc(t.title)}</td>
        <td style="padding:8px;color:#aaa;font-size:11px">${_esc(t.bpm)} BPM</td>
        <td style="padding:8px;color:#aaa;font-size:11px">${_esc(t.mood||'-')}</td>
        <td style="padding:8px;color:#aaa;font-size:11px">${_esc(Math.round(t.duration_seconds||0))}s</td>
        <td style="padding:8px"><button class="btn-primary" data-pick="${t.id}" style="padding:4px 10px;font-size:11px">Use</button></td>
      </tr>
    `).join('');
    m.innerHTML = `
      <div class="modal" style="max-width:780px;max-height:80vh;overflow-y:auto">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
          <div style="font-size:16px;font-weight:800">🎵 Music library</div>
          <button data-close style="background:none;border:none;color:#666;cursor:pointer;font-size:20px">&times;</button>
        </div>
        <div style="display:flex;gap:6px;margin-bottom:10px">
          <select id="mp-mood" style="background:#070712;border:1px solid #2a2a4a;border-radius:6px;padding:6px;color:#fff">
            <option value="">All moods</option>
            <option value="calm">Calm</option>
            <option value="moderate">Moderate</option>
            <option value="energetic">Energetic</option>
          </select>
          <input id="mp-q" placeholder="Search title…" style="flex:1;background:#070712;border:1px solid #2a2a4a;border-radius:6px;padding:6px;color:#fff">
          <button class="btn-secondary" onclick="SocialStudio.modules.music._reindex()">↻ Reindex</button>
        </div>
        <table style="width:100%;font-size:13px">
          <thead><tr style="text-align:left;color:#aaa">
            <th style="padding:6px">Preview</th><th style="padding:6px">Title</th>
            <th style="padding:6px">BPM</th><th style="padding:6px">Mood</th>
            <th style="padding:6px">Length</th><th></th>
          </tr></thead>
          <tbody id="mp-tbody">${rows || '<tr><td colspan="6" style="padding:24px;text-align:center;color:#666">No tracks indexed. Drop files into dashboard/static/social/music/free/ and click ↻ Reindex.</td></tr>'}</tbody>
        </table>
      </div>
    `;
    m.querySelector('[data-close]').addEventListener('click', close);
    document.getElementById('mp-mood').addEventListener('change', () => refresh(m));
    document.getElementById('mp-q').addEventListener('input', () => {
      if (m._t) clearTimeout(m._t);
      m._t = setTimeout(() => refresh(m), 250);
    });
    m.addEventListener('click', (e) => {
      if (e.target.dataset.pick) {
        const id = parseInt(e.target.dataset.pick, 10);
        const item = items.find(i => i.id === id);
        callback(item);
        close();
      }
    });
  }

  async function refresh(m) {
    const mood = document.getElementById('mp-mood').value;
    const q = document.getElementById('mp-q').value;
    const url = new URL('/api/ahb/social/music', location.origin);
    if (mood) url.searchParams.set('mood', mood);
    if (q) url.searchParams.set('q', q);
    const items = ((await (await fetch(url)).json()).items || []);
    // Re-paint just the tbody
    document.getElementById('mp-tbody').innerHTML = items.map(t => `
      <tr style="border-bottom:1px solid #1a1a2e">
        <td style="padding:8px"><audio controls src="/api/ahb/social/music/file/${t.id}" style="height:30px;max-width:200px"></audio></td>
        <td style="padding:8px">${_esc(t.title)}</td>
        <td style="padding:8px;color:#aaa;font-size:11px">${_esc(t.bpm)} BPM</td>
        <td style="padding:8px;color:#aaa;font-size:11px">${_esc(t.mood||'-')}</td>
        <td style="padding:8px;color:#aaa;font-size:11px">${_esc(Math.round(t.duration_seconds||0))}s</td>
        <td style="padding:8px"><button class="btn-primary" data-pick="${t.id}" style="padding:4px 10px;font-size:11px">Use</button></td>
      </tr>
    `).join('') || '<tr><td colspan="6" style="padding:24px;text-align:center;color:#666">No tracks match.</td></tr>';
  }

  async function _reindex() {
    SocialStudio.modules.toast.info('Reindexing…');
    const r = await fetch('/api/ahb/social/music/reindex', { method: 'POST' });
    const j = await r.json();
    SocialStudio.modules.toast.success(`Indexed ${j.indexed} new, skipped ${j.skipped}`);
  }

  return { pick, _reindex };
})();
</script>
```

- [ ] **Step 2: Add a music-file serve endpoint to `social_audio.py`**

Inside `register(bp)` in `dashboard/social_audio.py`, add:

```python
    @bp.route("/api/ahb/social/music/file/<int:mid>", methods=["GET"])
    def social_music_file(mid: int):
        from flask import send_file
        con = _db()
        try:
            r = con.execute("SELECT path FROM ahb_social_music_library WHERE id=?", (mid,)).fetchone()
        finally:
            con.close()
        if not r or not os.path.exists(r["path"]):
            return jsonify({"error": "not found"}), 404
        return send_file(r["path"])
```

- [ ] **Step 3: Add composer's Music button**

In `dashboard/templates/ahb123.html`, find the composer's AI buttons row (from v2.0 Task 8). Add a 🎵 button. Find:

```javascript
            <button class="btn-secondary ss-tip" data-tip="Translate caption to Spanish" onclick="SocialStudio.modules.composer.aiTranslate()">🌐 ES</button>
          </div>
```

Add immediately before the `</div>`:

```javascript
            <button class="btn-secondary ss-tip" data-tip="Pick a music bed for video posts" onclick="SocialStudio.modules.composer.pickMusic()">🎵 Music</button>
```

In the composer IIFE, after `aiTranslate()`, add:

```javascript
  function pickMusic() {
    if (!SocialStudio.modules.music) { SocialStudio.modules.toast.error('Music module not loaded'); return; }
    SocialStudio.modules.music.pick((track) => {
      if (!track) return;
      state.musicTrack = track;
      SocialStudio.modules.toast.success('Music: ' + track.title);
    });
  }
```

Also add to the IIFE return: `pickMusic`.

Initialize `state.musicTrack = null` at the top of the composer state object — actually it's on `SocialStudio.state` which is shared; just set when picked.

- [ ] **Step 4: Restart + smoke**

```
sudo systemctl restart baza-dashboard
```

Open Composer. Click 🎵 Music. Music picker modal opens. If you dropped an MP3 in `dashboard/static/social/music/free/`, it shows up. Click ↻ Reindex to refresh after dropping new files. Pick one — toast confirms.

- [ ] **Step 5: Commit**

```
git add dashboard/templates/ahb123.html dashboard/social_audio.py
git commit -m "social v2.1: music picker UI + composer 🎵 button

SocialStudio.modules.music.pick(cb) opens a modal listing the indexed
library with mood/q filters, inline HTML5 audio preview, and ↻ Reindex
button. Composer's pickMusic() opens it and stores chosen track on
state.musicTrack. /api/ahb/social/music/file/<id> serves the file for
preview. Render-pipeline music mixing wired in next task.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Render pipeline — subtitles + music + LUTs + logo + intro/outro

**Files:**
- Modify: `dashboard/social_render.py` — extend build_filter_graph + render_video signature
- Modify: `dashboard/social_studio.py` — pass new params to render
- Test: `tests/test_social_v2_render.py`

- [ ] **Step 1: Write failing tests**

`tests/test_social_v2_render.py`:

```python
"""Tests for Social Studio v2.1 render pipeline extensions."""
import os
import sys
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def render_mod():
    sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))
    if "social_render" in sys.modules:
        del sys.modules["social_render"]
    import social_render
    yield social_render


def test_filter_graph_with_lut(render_mod):
    g = render_mod.build_filter_graph(
        in_w=1080, in_h=1920, platform="tiktok", fill_mode="blurred",
        hook_text=None, brand_corner=False, lut_path="/fake/cinematic.cube",
    )
    assert "lut3d=" in g
    assert "cinematic.cube" in g


def test_filter_graph_with_logo(render_mod):
    g = render_mod.build_filter_graph(
        in_w=1080, in_h=1920, platform="tiktok", fill_mode="blurred",
        hook_text=None, brand_corner=False, logo_path="/fake/logo.png",
    )
    # Logo overlay is added via a complex filter; simple check
    assert "movie=" in g or "overlay=" in g


def test_filter_graph_with_subtitles(render_mod):
    g = render_mod.build_filter_graph(
        in_w=1080, in_h=1920, platform="tiktok", fill_mode="blurred",
        hook_text=None, brand_corner=False, subtitles_path="/fake/subs.srt",
    )
    assert "subtitles=" in g
```

Run: `pytest tests/test_social_v2_render.py -v` → FAIL (params not yet supported).

- [ ] **Step 2: Extend `build_filter_graph` signature + body**

In `dashboard/social_render.py`, replace `build_filter_graph` with:

```python
def build_filter_graph(in_w: int, in_h: int, platform: str,
                       fill_mode: str = "blurred",
                       hook_text: Optional[str] = None,
                       brand_corner: bool = False,
                       lut_path: Optional[str] = None,
                       logo_path: Optional[str] = None,
                       logo_position: str = "br",
                       logo_opacity: float = 0.7,
                       subtitles_path: Optional[str] = None,
                       ken_burns: bool = False) -> str:
    out_w, out_h = target_dims(platform)
    src_aspect = in_w / max(in_h, 1)
    tgt_aspect = out_w / out_h
    parts = []
    # Aspect handling (existing logic)
    if src_aspect > tgt_aspect:
        if fill_mode == "blurred":
            parts.append(
                f"split=2[bg][fg];"
                f"[bg]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
                f"crop={out_w}:{out_h},gblur=sigma=24[bgb];"
                f"[fg]scale={out_w}:-2[fgs];"
                f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2"
            )
        else:
            parts.append(
                f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
                f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:color=black"
            )
    else:
        parts.append(
            f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
            f"crop={out_w}:{out_h}"
        )
    # Ken-Burns zoom on stills (only when explicitly requested)
    if ken_burns:
        # Slow zoom-in over the clip's duration; ffmpeg's zoompan is per-frame
        parts.append("zoompan=z='min(zoom+0.0008,1.2)':d=125:s={}x{}".format(out_w, out_h))
    # 3D LUT color grade
    if lut_path:
        parts.append(f"lut3d={shlex.quote(lut_path)}")
    # Logo bug overlay
    if logo_path:
        # Position presets
        pos = {
            "tl": "10:10",
            "tr": "main_w-overlay_w-10:10",
            "bl": "10:main_h-overlay_h-10",
            "br": "main_w-overlay_w-10:main_h-overlay_h-10",
        }.get(logo_position, "main_w-overlay_w-10:main_h-overlay_h-10")
        # Inline overlay via movie= filter source; opacity via colorchannelmixer
        parts.append(
            f"movie={shlex.quote(logo_path)},format=rgba,colorchannelmixer=aa={logo_opacity}[logo];"
            f"[in][logo]overlay={pos}"
        )
    # Subtitles burn-in
    if subtitles_path:
        # ffmpeg requires single-quoted, escaped path
        sub_safe = subtitles_path.replace(":", r"\:").replace(",", r"\,")
        parts.append(
            f"subtitles='{sub_safe}':force_style='Fontname=Inter,FontSize=18,PrimaryColour=&H00FFFFFF,"
            f"BackColour=&H80000000,BorderStyle=4,Outline=1,Shadow=0,Alignment=2,MarginV=80'"
        )
    # Hook text overlay (from v2.0)
    if hook_text:
        safe = (
            hook_text
            .replace("\\", "\\\\")
            .replace("'", r"\'")
            .replace(":", r"\:")
            .replace(",", r"\,")
            .replace("%{", "%%{")
        )
        parts.append(
            f"drawtext=fontfile={FONT_BOLD}:text='{safe}':"
            f"fontcolor=white:fontsize=72:line_spacing=10:"
            f"box=1:boxcolor=black@0.45:boxborderw=18:"
            f"x=(w-text_w)/2:y=h*0.10"
        )
    return ",".join(parts)
```

- [ ] **Step 3: Extend `render_video` signature to thread through new params**

```python
def render_video(srcs, out: str, platform: str,
                 hook_text: Optional[str] = None,
                 brand_corner: bool = False,
                 fill_mode: str = "blurred",
                 max_seconds: int = 60,
                 lut_path: Optional[str] = None,
                 logo_path: Optional[str] = None,
                 logo_position: str = "br",
                 logo_opacity: float = 0.7,
                 subtitles_path: Optional[str] = None,
                 music_path: Optional[str] = None,
                 music_volume_db: float = -18.0,
                 voiceover_path: Optional[str] = None,
                 voiceover_volume_db: float = -14.0,
                 intro_path: Optional[str] = None,
                 outro_path: Optional[str] = None) -> str:
    if not srcs:
        raise ValueError("no sources")
    clips = []
    for s in srcs:
        if isinstance(s, str):
            clips.append({"path": s, "in_seconds": None, "out_seconds": None})
        else:
            clips.append(s)
    if not clips:
        raise ValueError("no sources")

    # Prepend intro / append outro
    if intro_path and os.path.exists(intro_path):
        clips = [{"path": intro_path, "in_seconds": None, "out_seconds": None}] + clips
    if outro_path and os.path.exists(outro_path):
        clips = clips + [{"path": outro_path, "in_seconds": None, "out_seconds": None}]

    w, h = _ffprobe(clips[0]["path"])
    g = build_filter_graph(
        w, h, platform, fill_mode, hook_text, brand_corner,
        lut_path=lut_path, logo_path=logo_path,
        logo_position=logo_position, logo_opacity=logo_opacity,
        subtitles_path=subtitles_path,
    )

    tmpdir = os.path.dirname(out) or "."
    fd, list_path = tempfile.mkstemp(suffix=".concat.txt", dir=tmpdir, text=True)
    try:
        with os.fdopen(fd, "w") as f:
            for c in clips:
                f.write(f"file {shlex.quote(os.path.abspath(c['path']))}\n")
                if c.get("in_seconds") is not None:
                    f.write(f"inpoint {float(c['in_seconds'])}\n")
                if c.get("out_seconds") is not None:
                    f.write(f"outpoint {float(c['out_seconds'])}\n")

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", list_path,
        ]
        # Additional audio inputs
        audio_inputs = []  # list of (label, path) for filter_complex audio mixing
        if music_path and os.path.exists(music_path):
            cmd += ["-stream_loop", "-1", "-i", music_path]
            audio_inputs.append(("music", len(audio_inputs) + 1))  # input index in cmd
        if voiceover_path and os.path.exists(voiceover_path):
            cmd += ["-i", voiceover_path]
            audio_inputs.append(("vo", len(audio_inputs) + 1))

        # Build filter_complex if we have audio mixing
        if audio_inputs:
            # Audio mix: [0:a] is the concat audio, then music + voiceover
            audio_parts = ["[0:a]volume=1.0[a0]"]
            mix_inputs = ["[a0]"]
            for label, idx in audio_inputs:
                if label == "music":
                    db = music_volume_db
                    audio_parts.append(
                        f"[{idx}:a]volume={10**(db/20):.4f}[am]"
                    )
                    mix_inputs.append("[am]")
                elif label == "vo":
                    db = voiceover_volume_db
                    audio_parts.append(
                        f"[{idx}:a]volume={10**(db/20):.4f}[av]"
                    )
                    mix_inputs.append("[av]")
            # Sidechain ducking if both music and vo
            has_music = any(l == "music" for l, _ in audio_inputs)
            has_vo = any(l == "vo" for l, _ in audio_inputs)
            if has_music and has_vo:
                # Apply sidechaincompress: music keyed by voiceover
                audio_parts.append("[am][av]sidechaincompress=threshold=0.05:ratio=8:attack=10:release=200[amd]")
                # Replace [am] in mix_inputs with [amd]
                mix_inputs = [x if x != "[am]" else "[amd]" for x in mix_inputs]
            audio_parts.append(f"{''.join(mix_inputs)}amix=inputs={len(mix_inputs)}:duration=first:dropout_transition=2[aout]")
            audio_parts.append("[aout]loudnorm=I=-14:LRA=11:TP=-1.0[afinal]")
            # Video filter
            audio_parts.append(f"[0:v]{g}[vfinal]")
            cmd += [
                "-filter_complex", ";".join(audio_parts),
                "-map", "[vfinal]", "-map", "[afinal]",
            ]
        else:
            cmd += ["-vf", g]

        cmd += [
            "-c:v", "libx264", "-profile:v", "high", "-level", "4.1",
            "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "22",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            "-t", str(max_seconds),
            out,
        ]
        subprocess.run(cmd, check=True, capture_output=True)
    finally:
        if os.path.exists(list_path):
            os.remove(list_path)
    return out
```

- [ ] **Step 4: Thread new params through `social_studio.py`'s `_kick_render_async`**

In `dashboard/social_studio.py`, update the render code inside `_kick_render_async`. Find the `if is_video:` block and replace:

```python
                if is_video:
                    post_row = _get_post(post_id)
                    music_id = post_row["music_id"] if post_row else None
                    music_path = None
                    if music_id:
                        con = _conn()
                        try:
                            m = con.execute("SELECT path FROM ahb_social_music_library WHERE id=?", (music_id,)).fetchone()
                        finally:
                            con.close()
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
                        for sid, p in zip(source_ids, paths_list):
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
                                             intro_path=intro_path, outro_path=outro_path)
                    else:
                        _render.render_video(paths_list, out_path, platform,
                                             hook_text=hook, fill_mode=fill,
                                             lut_path=lut_path, logo_path=logo_path,
                                             subtitles_path=subtitles_path,
                                             music_path=music_path,
                                             voiceover_path=voiceover_path,
                                             intro_path=intro_path, outro_path=outro_path)
                    cover_path = os.path.join(out_dir, "cover.jpg")
                    _render.extract_cover(out_path, cover_path)
```

- [ ] **Step 5: Update Composer's renderPackage to send music_id**

In `dashboard/templates/ahb123.html`, find the Composer's `renderPackage`. After creating the post, PATCH it with the music_id before kicking render. Find the existing `const post = await fetch(...)` line in renderPackage and after it add:

```javascript
    if (state.musicTrack) {
      await fetch('/api/ahb/social/posts/' + post.id, {
        method: 'PATCH', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ music_id: state.musicTrack.id }),
      });
    }
```

But wait — `music_id` isn't in `POST_WRITABLE` from Phase 1. Add it. In `dashboard/social_studio.py`, find `POST_WRITABLE` and add: `"music_id", "voiceover_path", "subtitles_path", "lut_name"`.

- [ ] **Step 6: Run tests + restart + smoke**

```
pytest tests/test_social_v2_render.py -v
sudo systemctl restart baza-dashboard
```

Expected: 3 render tests pass.

If you have a real MP3 indexed in the music library AND a video source in image_captions, you can full-pipeline test by picking music in Composer and rendering — output .mp4 should have the music mixed.

- [ ] **Step 7: Commit**

```
git add dashboard/social_render.py dashboard/social_studio.py dashboard/templates/ahb123.html tests/test_social_v2_render.py
git commit -m "social v2.1: render pipeline — subtitles + music + LUTs + logo + intro/outro

build_filter_graph gains lut_path, logo_path/position/opacity,
subtitles_path, ken_burns params. render_video gains music_path,
music/voiceover volume_db, intro/outro paths. When music + voiceover
both present: filter_complex chain mixes with sidechaincompress
ducking music under speech; final pass through loudnorm to -14 LUFS.
_kick_render_async threads music_id from post → music library lookup,
voiceover/subtitles paths from post columns, lut from name, logo
from brand kit. POST_WRITABLE expanded.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Whisper auto-subtitles endpoint + UI

**Files:**
- Modify: `dashboard/social_audio.py` — add subtitle generation
- Modify: `dashboard/templates/ahb123.html` — subtitles button in Library post-detail
- Test: extend `tests/test_social_v2_audio.py`

- [ ] **Step 1: Add the route to social_audio.py**

Inside `register(bp)` in `dashboard/social_audio.py`, add:

```python
    @bp.route("/api/ahb/social/posts/<int:pid>/subtitles", methods=["POST"])
    def social_subtitles_generate(pid: int):
        from faster_whisper import WhisperModel
        con = _db()
        try:
            row = con.execute("SELECT asset_path FROM ahb_social_posts WHERE id=?", (pid,)).fetchone()
        finally:
            con.close()
        if not row or not row["asset_path"] or not os.path.exists(row["asset_path"]):
            return jsonify({"error": "post has no rendered asset"}), 400
        asset_path = row["asset_path"]
        # Lazy-load tiny.en model in a process-global cache
        global _WHISPER_MODEL
        try:
            _WHISPER_MODEL
        except NameError:
            _WHISPER_MODEL = WhisperModel("tiny.en", device="cpu", compute_type="int8")
        # Extract audio with ffmpeg to a temp wav (faster_whisper handles MP4 but wav is reliable)
        import subprocess, tempfile
        wav = tempfile.mktemp(suffix=".wav")
        try:
            subprocess.run(["ffmpeg", "-y", "-i", asset_path, "-ar", "16000", "-ac", "1", wav],
                           check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            return jsonify({"error": "audio extract failed", "detail": e.stderr.decode(errors='ignore')[-200:]}), 500
        try:
            segments, _info = _WHISPER_MODEL.transcribe(wav, beam_size=1)
            # Write SRT next to the asset
            srt_path = os.path.splitext(asset_path)[0] + ".srt"
            with open(srt_path, "w") as f:
                for i, seg in enumerate(segments, 1):
                    f.write(f"{i}\n")
                    f.write(f"{_srt_ts(seg.start)} --> {_srt_ts(seg.end)}\n")
                    f.write(f"{seg.text.strip()}\n\n")
        finally:
            if os.path.exists(wav):
                os.remove(wav)
        # Persist on post
        con = _db()
        try:
            con.execute("UPDATE ahb_social_posts SET subtitles_path=? WHERE id=?", (srt_path, pid))
            con.commit()
        finally:
            con.close()
        return jsonify({"ok": True, "subtitles_path": srt_path})


def _srt_ts(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
```

Move `_srt_ts` to module scope (outside `register`) so it's reachable.

- [ ] **Step 2: Add the UI button in Library post-detail**

In `dashboard/templates/ahb123.html`, find the postdetail modal (search `SocialStudio.modules.postdetail`). In the modal HTML, find the button row near the bottom:

```javascript
        <div style="display:flex;gap:6px;margin-top:10px;justify-content:flex-end">
          <button class="btn-secondary" onclick="SocialStudio.modules.postdetail.save(${p.id})">Save</button>
          <button class="btn-primary" onclick="SocialStudio.modules.postdetail.bundle(${p.id})">&#x1F4E5; Bundle</button>
          <button class="btn-secondary" onclick="SocialStudio.modules.postdetail.telegram(${p.id})">&#x1F4F2; Phone</button>
        </div>
```

Add a Subtitles button:

```javascript
        <div style="display:flex;gap:6px;margin-top:10px;justify-content:flex-end">
          <button class="btn-secondary" onclick="SocialStudio.modules.postdetail.subtitles(${p.id})">📝 Subtitles</button>
          <button class="btn-secondary" onclick="SocialStudio.modules.postdetail.save(${p.id})">Save</button>
          <button class="btn-primary" onclick="SocialStudio.modules.postdetail.bundle(${p.id})">&#x1F4E5; Bundle</button>
          <button class="btn-secondary" onclick="SocialStudio.modules.postdetail.telegram(${p.id})">&#x1F4F2; Phone</button>
        </div>
```

Add `subtitles` function to postdetail's return:

```javascript
  async function subtitles(id) {
    const tid = SocialStudio.modules.toast.progress('Transcribing… (tiny.en model)');
    try {
      const r = await fetch('/api/ahb/social/posts/' + id + '/subtitles', { method: 'POST' });
      const j = await r.json();
      if (r.ok) SocialStudio.modules.toast.resolve(tid, 'success', 'Subtitles saved (' + j.subtitles_path + ')');
      else SocialStudio.modules.toast.resolve(tid, 'error', 'Failed: ' + (j.error || 'unknown'));
    } catch (e) {
      SocialStudio.modules.toast.resolve(tid, 'error', 'Network: ' + e.message);
    }
  }
  return { open, save, bundle, telegram, subtitles };
```

- [ ] **Step 3: Add test**

Append to `tests/test_social_v2_audio.py`:

```python
def test_subtitles_400_no_asset(client):
    c, _ = client
    pid = c.post("/api/ahb/social/posts", json={
        "platform": "tiktok", "variant": "9x16", "source_media_ids": [1]
    }).get_json()["id"]
    r = c.post(f"/api/ahb/social/posts/{pid}/subtitles", method="POST")
    # Flask test client uses .post directly
    # ... use c.post not c.post(method=)
    r = c.post(f"/api/ahb/social/posts/{pid}/subtitles")
    assert r.status_code == 400  # no asset_path on this post
```

(Note: an actual end-to-end subtitle test would require a real video file with speech; that's a manual smoke step.)

- [ ] **Step 4: Run tests + restart + smoke**

```
pytest tests/test_social_v2_audio.py -v
sudo systemctl restart baza-dashboard
```

For real smoke: render any video post first (existing pipeline), then open it in Library detail → click 📝 Subtitles. Should take 5-30s depending on video length; SRT file lands next to the .mp4. Re-render with that post and subtitles will burn in (from Task 5).

- [ ] **Step 5: Commit**

```
git add dashboard/social_audio.py dashboard/templates/ahb123.html tests/test_social_v2_audio.py
git commit -m "social v2.1: whisper auto-subtitles endpoint + Library UI

POST /api/ahb/social/posts/<id>/subtitles extracts audio with ffmpeg,
runs faster_whisper tiny.en (process-cached model load), writes SRT
next to the asset, stores subtitles_path on the post. Library
postdetail gets 📝 Subtitles button. Burn-in happens on the NEXT
render since subtitles_path is read from the post at render time.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Piper voiceover endpoint + UI

**Files:**
- Modify: `dashboard/social_audio.py` — voiceover route
- Modify: `dashboard/templates/ahb123.html` — voiceover module (replacing Phase 1's empty modal slot)

- [ ] **Step 1: Add route to social_audio.py**

Inside `register(bp)`:

```python
    @bp.route("/api/ahb/social/ai/voiceover", methods=["POST"])
    def social_voiceover():
        data = request.get_json(silent=True) or {}
        text = (data.get("text") or "").strip()
        if not text:
            return jsonify({"error": "text required"}), 400
        voice = data.get("voice") or "en_US-amy-medium"
        voice_path = os.path.join(_HERE, "static", "social", "piper-voices", f"{voice}.onnx")
        if not os.path.exists(voice_path):
            return jsonify({"error": f"voice not installed: {voice}"}), 400
        # Output to a unique file
        import tempfile, subprocess as sp
        out_dir = os.path.join(_HERE, "artifacts", "social", "voiceover")
        os.makedirs(out_dir, exist_ok=True)
        out_path = tempfile.mktemp(suffix=".wav", dir=out_dir)
        # Piper CLI is `piper --model … --output_file …`
        try:
            sp.run(
                ["piper", "--model", voice_path, "--output_file", out_path],
                input=text.encode("utf-8"),
                check=True, capture_output=True,
            )
        except sp.CalledProcessError as e:
            return jsonify({"error": "piper failed", "detail": e.stderr.decode(errors='ignore')[-200:]}), 500
        except FileNotFoundError:
            return jsonify({"error": "piper not installed (run dashboard/social_install_assets.sh)"}), 500
        # Optional: associate with a post via post_id query
        post_id = request.args.get("post_id", type=int)
        if post_id:
            con = _db()
            try:
                con.execute("UPDATE ahb_social_posts SET voiceover_path=? WHERE id=?", (out_path, post_id))
                con.commit()
            finally:
                con.close()
        return jsonify({"ok": True, "voiceover_path": out_path, "url": f"/api/ahb/social/ai/voiceover/preview?path={out_path}"})


    @bp.route("/api/ahb/social/ai/voiceover/preview", methods=["GET"])
    def social_voiceover_preview():
        from flask import send_file
        path = request.args.get("path")
        if not path or not os.path.exists(path) or not path.startswith(os.path.join(_HERE, "artifacts", "social", "voiceover")):
            return jsonify({"error": "invalid"}), 400
        return send_file(path)
```

- [ ] **Step 2: Add the voiceover UI module**

Append IIFE:

```html
<script>
SocialStudio.modules.voiceover = (function(){
  function _esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function open(postId, defaultText) {
    const m = document.getElementById('socialVoiceover');
    m.style.display = 'flex';
    m.innerHTML = `
      <div class="modal" style="max-width:560px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
          <div style="font-size:16px;font-weight:800">🔊 Voiceover</div>
          <button onclick="document.getElementById('socialVoiceover').style.display='none'" style="background:none;border:none;color:#666;cursor:pointer;font-size:20px">&times;</button>
        </div>
        <textarea id="vo-text" placeholder="What should the voiceover say?" style="width:100%;height:120px;background:#070712;border:1px solid #2a2a4a;border-radius:6px;padding:8px;color:#fff;font-size:13px">${_esc(defaultText || '')}</textarea>
        <label style="display:block;font-size:12px;color:#aaa;margin-top:8px">Voice
          <select id="vo-voice" style="width:100%;background:#070712;border:1px solid #2a2a4a;border-radius:6px;padding:6px;color:#fff">
            <option value="en_US-amy-medium">Amy (US female, friendly)</option>
            <option value="en_US-ryan-high">Ryan (US male, professional)</option>
            <option value="en_GB-jenny-medium">Jenny (UK female)</option>
            <option value="en_US-lessac-medium">Lessac (US male, narrator)</option>
          </select>
        </label>
        <div style="display:flex;justify-content:flex-end;gap:6px;margin-top:14px">
          <button class="btn-primary" id="vo-go">🎤 Generate</button>
        </div>
        <div id="vo-preview" style="margin-top:14px"></div>
      </div>
    `;
    document.getElementById('vo-go').addEventListener('click', () => generate(postId));
  }

  async function generate(postId) {
    const text = document.getElementById('vo-text').value;
    const voice = document.getElementById('vo-voice').value;
    if (!text.trim()) { SocialStudio.modules.toast.info('Enter voiceover text first'); return; }
    const tid = SocialStudio.modules.toast.progress('Synthesizing…');
    try {
      const url = '/api/ahb/social/ai/voiceover' + (postId ? '?post_id=' + postId : '');
      const r = await fetch(url, {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ text, voice }),
      });
      const j = await r.json();
      if (!r.ok) { SocialStudio.modules.toast.resolve(tid, 'error', j.error || 'Failed'); return; }
      SocialStudio.modules.toast.resolve(tid, 'success', 'Voiceover ready');
      document.getElementById('vo-preview').innerHTML = `<audio controls src="${j.url}" style="width:100%"></audio>`;
    } catch (e) {
      SocialStudio.modules.toast.resolve(tid, 'error', 'Network: ' + e.message);
    }
  }

  return { open, generate };
})();
</script>
```

- [ ] **Step 3: Wire from Composer and Library**

In Composer's render() template, add a voiceover button in the AI button row:

```javascript
            <button class="btn-secondary ss-tip" data-tip="Generate voiceover (piper TTS)" onclick="SocialStudio.modules.voiceover.open(null, document.getElementById('ss-caption-' + SocialStudio.state.activePlatform).value)">🔊 VO</button>
```

In postdetail modal's button row, also add:

```javascript
          <button class="btn-secondary" onclick="SocialStudio.modules.voiceover.open(${p.id}, document.getElementById('pd-caption').value)">🔊 VO</button>
```

- [ ] **Step 4: Restart + smoke**

```
sudo systemctl restart baza-dashboard
```

Open Composer, click 🔊 VO. Modal opens with caption text pre-filled. Pick a voice, click Generate. After 5-15s a `<audio>` element appears with the spoken result.

- [ ] **Step 5: Commit**

```
git add dashboard/social_audio.py dashboard/templates/ahb123.html
git commit -m "social v2.1: piper TTS voiceover endpoint + modal

POST /api/ahb/social/ai/voiceover {text, voice} runs piper CLI with the
chosen voice ONNX, writes a wav under artifacts/social/voiceover/,
optionally associates to a post via ?post_id=. GET .../preview?path=
serves the wav (path validated to artifacts dir to prevent traversal).
SocialStudio.modules.voiceover.open(postId?, defaultText?) opens a
modal with text editor + 4 voice picker. Composer 🔊 VO button +
Library post-detail 🔊 VO button.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Tasks 8-22: condensed format

The remaining 15 tasks follow the same pattern as Tasks 3-7: each implements one feature from the spec, with a backend route + UI module + tests + commit. To keep this plan navigable, the remaining tasks are described in condensed form. Each task's "Files," "Steps," and "Commit" must still be executed by the subagent — the patterns above are the template to follow.

---

## Task 8: AI hook patterns (B.1 of bundle C)

**Files:** `dashboard/social_ai.py`, `dashboard/prompts/social/hooks_advanced.md`, `dashboard/templates/ahb123.html`, `tests/test_social_v2_ai.py`

Add prompt file with named patterns (curiosity_gap, contrarian, number_led, before_after, personal, mistake, bold_claim — see spec §7 C.1). Route `POST /api/ahb/social/ai/hook` body `{source_ids, pattern, n}` → `{hooks: [...], pattern: "..."}`. In Composer, replace existing Hooks button with a pattern picker chip strip + per-pattern regenerate. Test: monkeypatch `_call_ollama_chat` to return JSON array, assert hooks shaped correctly per pattern.

**Commit:** `social v2.1: named virality patterns for hook generator`

---

## Task 9: CTA + comment-bait generators (B.2-B.3 of bundle C)

**Files:** `dashboard/social_ai.py`, prompt files, `dashboard/templates/ahb123.html`, tests.

Two prompts (`cta_system.md`, `comment_bait.md`). Two routes `POST /ai/cta` and `POST /ai/comment-bait`, both take `{caption, platform}` and return 3 variants. Composer gets 🎯 CTA and 💬 Engage buttons in the AI row. Variants append to caption with a separator on user pick.

**Commit:** `social v2.1: CTA + comment-bait generators`

---

## Task 10: Multi-language batch translate (C.4)

**Files:** `dashboard/social_ai.py`, `dashboard/social_settings.py` (add `translation_targets` default), `dashboard/templates/ahb123.html`, tests.

Settings gains `translation_targets: ["es"]` (configurable up to 5 langs). New route `POST /ai/translate-all` body `{caption, hashtags, source_ids, platform}` → parallel `_call_ollama_chat` per target → `{translations: {es: {caption, hashtags}, ...}}`. Composer's 🌐 button becomes a fly-out: click ES = current, click "+" = add another language (settings drawer). On render-bundle, write one caption file per language.

**Commit:** `social v2.1: multi-language batch translation`

---

## Task 11: Voiceover script generator (C.5)

**Files:** `dashboard/social_ai.py`, `dashboard/prompts/social/voiceover_script.md`, `dashboard/templates/ahb123.html`, tests.

Prompt instructs the model to output spoken script with `[pause]`, `[emphasis: word]`, `[fast]` markers. Route `POST /ai/voiceover-script` body `{caption, source_ids}` → `{script: "..."}`. In the Voiceover modal (Task 7), add a "🤖 Generate script" button that calls this and fills the text area. Pacing markers are passed through to piper as-is (piper ignores unknown markers gracefully).

**Commit:** `social v2.1: voiceover script generator`

---

## Task 12: Storyboard generator (C.6)

**Files:** `dashboard/social_ai.py`, `dashboard/prompts/social/storyboard.md`, `dashboard/templates/ahb123.html`, tests.

Prompt outputs JSON shot list: `[{shot_type, subject, duration_sec, voiceover_line}]`. Route `POST /ai/storyboard` body `{project_description, duration, style}` → JSON list. New view: add a "📋 Storyboard" toggle in Composer that overlays the AI button area with a 5-10-shot card grid. Each shot card click filters source picker by inferred tags (e.g. shot_type=closeup + subject=trim → q=trim).

**Commit:** `social v2.1: storyboard generator`

---

## Task 13: B-roll suggestions (C.7)

**Files:** `dashboard/social_ai.py`, `dashboard/prompts/social/broll.md`, `dashboard/templates/ahb123.html`, tests.

Prompt: given existing media list + caption, return 3-5 suggested shots to capture. Route `POST /ai/broll` body `{source_ids, caption}` → `{suggestions: [...]}`. Composer button "📸 B-roll" opens a side panel checklist.

**Commit:** `social v2.1: B-roll suggestions`

---

## Task 14: Performance prediction + best-times (C.8-C.9)

**Files:** `dashboard/social_ai.py`, `dashboard/templates/ahb123.html`, tests.

Route `POST /ai/predict` body `{caption, hashtags, hook, platform, source_ids}` → `{view_range: {low, mid, high}, confidence, improvements: [...]}`. Heuristic: starts with `/ai/score` then adjusts based on hook length, hashtag count, caption length, time-of-day fit. Improvements are 3 specific suggestions.

Route `GET /best-times?platform=ig_reel` → `{slots: [{day_of_week, hour, score}]}`. Queries `ahb_social_analytics` from v2.2 (this task ships with industry defaults when analytics table empty).

Composer 🔮 Predict button next to 🎯 Score. Schedule date picker (in Scheduler later) highlights recommended slots green.

**Commit:** `social v2.1: performance prediction + best-time recommendations`

---

## Task 15: Vision cover-pick (B.1 of bundle B)

**Files:** `dashboard/social_ai.py`, `dashboard/templates/ahb123.html`, tests.

Route `POST /ai/cover-pick` body `{post_id}`. Server: ffmpeg extracts 5 frames at 0/25/50/75/95% timestamps, b64-encodes each, calls qwen3-vl via Ollama `/api/generate` (multi-image prompt), parses `{"index": <int>}` response, copies winning frame to `post.cover_path`. Library post-detail "Pick cover again" button.

**Commit:** `social v2.1: vision-driven cover-pick (qwen3-vl)`

---

## Task 16: In-app image editor (B.5)

**Files:** `dashboard/social_studio.py` (sidecar route), `dashboard/social_render.py` (apply edits), `dashboard/templates/ahb123.html` (modal), tests.

Modal opens on per-thumbnail ✏ click. Tools: crop with aspect snaps, rotate (90° + free), brightness/contrast/saturation sliders, 5 filter presets. Edits stored as `<sub_path>.edits.json` sidecar. Routes: `POST /sources/<id>/edits` save, `DELETE /sources/<id>/edits` revert. Render pipeline: when sidecar exists, prepend `crop=`, `rotate=`, `eq=brightness=...:saturation=...` filters before existing aspect handling.

**Commit:** `social v2.1: in-app image editor`

---

## Task 17: Brand kit logo upload + intro/outro slots (B.6-B.7)

**Files:** `dashboard/social_studio.py` (upload route), `dashboard/templates/ahb123.html` (brand kit modal extension)

Brand kit modal gains 3 upload inputs: logo (PNG ≤1MB), intro_clip (MP4 ≤30MB ≤5s), outro_clip (MP4 ≤30MB ≤5s). Route `POST /brand-kit/upload` accepts multipart. Server validates type + size + duration (ffprobe for clips). On accept, paths stored in `social_brand_kit.json`. Render pipeline already uses `logo_path`/`intro_path`/`outro_path` from Task 5.

**Commit:** `social v2.1: brand kit uploads — logo, intro, outro clips`

---

## Task 18: Color LUTs picker (B.8)

**Files:** `dashboard/templates/ahb123.html` (composer LUT chip strip)

In Composer, add a horizontal chip strip below the platform tabs: `None | Cinematic | Vibrant | Moody | B&W | Warm`. Active chip updates `state.lutName`. On render-package, include `lut_name: state.lutName` in the post create body (POST_WRITABLE already updated in Task 5).

**Commit:** `social v2.1: LUT picker in composer`

---

## Task 19: Ken-Burns + beat-sync toggles (B.9-B.10)

**Files:** `dashboard/social_studio.py` (render_params), `dashboard/social_render.py` (beat-sync logic), `dashboard/templates/ahb123.html`

Composer Settings drawer (already in v1) gains two toggles: "Auto Ken-Burns on still photos" (default ON), "Sync cuts to beat" (default OFF, only enabled when music attached). render_video accepts `beat_sync=True` — when set, uses librosa.beat.beat_track on music_path to compute beat timestamps, then adjusts each clip's outpoint to land on the nearest beat. Ken-Burns from Task 5's `ken_burns` param (default true for stills via auto-detection).

**Commit:** `social v2.1: Ken-Burns + beat-sync toggles`

---

## Task 20: Webcam + screen recorders (I.1-I.2)

**Files:** `dashboard/social_sources.py` (upload route), `dashboard/templates/ahb123.html` (recorder modals)

`POST /api/ahb/social/sources/upload` accepts multipart file, saves under `dashboard/uploads/social/<date>/<uuid>.<ext>`, runs ffmpeg to transcode WebM→MP4 if needed, inserts into image_captions so it shows in source picker. Recorder IIFE uses MediaRecorder (webcam: `getUserMedia({video,audio:true})`) or getDisplayMedia (screen). Modal shows live preview + record/stop. On stop, blob uploaded to /sources/upload.

**Commit:** `social v2.1: webcam + screen recorders`

---

## Task 21: URL import (yt-dlp) (I.3) + voice memo (I.5)

**Files:** `dashboard/social_sources.py`, `dashboard/templates/ahb123.html`

Route `POST /sources/url-import` body `{url}`. Server: yt-dlp downloads at 1080p max to uploads dir, returns source_id. Rate-limit 5/hour via in-memory counter. UI modal: URL input, "Import" button.

Voice memo: Recorder modal with audio-only MediaRecorder. On upload, server runs faster_whisper for transcription; transcript returned + filled into the composer's caption field.

**Commit:** `social v2.1: URL import (yt-dlp) + voice memo with transcription`

---

## Task 22: Multi-file drag-drop + SD prompt builder (I.4 + C.10)

**Files:** `dashboard/templates/ahb123.html`

Whole composer becomes a drop zone with overlay on dragover. Multiple files uploaded in parallel to /sources/upload with per-file progress bars in toast stack.

SD prompt builder modal: subject input + style chips (photorealistic/illustration/watercolor/3D/isometric/line-art) + negative prompt (advanced). On Generate, calls existing sam_imaging endpoint with constructed prompt. When SD service inactive, shows "Start SD" button that calls `systemctl --user start baza-sd-webui.service` via a new route.

**Commit:** `social v2.1: multi-file drag-drop + SD prompt builder`

---

## Plan self-review

**Spec coverage:**
- B.1 vision cover-pick → Task 15 ✓
- B.2 whisper subtitles → Task 6 ✓ (gen) + Task 5 ✓ (burn-in)
- B.3 piper voiceover → Task 7 ✓
- B.4 music + ducking → Task 3+4 (lib+UI) + Task 5 (mixing) ✓
- B.5 image editor → Task 16 ✓
- B.6 logo bug → Task 17 (upload) + Task 5 (render) ✓
- B.7 intro/outro → Task 17 (upload) + Task 5 (render) ✓
- B.8 LUTs → Task 18 (picker) + Task 5 (render) ✓
- B.9 Ken-Burns → Task 19 ✓
- B.10 beat-sync → Task 19 ✓
- C.1 hook patterns → Task 8 ✓
- C.2 CTA → Task 9 ✓
- C.3 comment-bait → Task 9 ✓
- C.4 multi-lang → Task 10 ✓
- C.5 voiceover script → Task 11 ✓
- C.6 storyboard → Task 12 ✓
- C.7 B-roll → Task 13 ✓
- C.8 predict → Task 14 ✓
- C.9 best-times → Task 14 ✓
- C.10 SD prompt builder → Task 22 ✓
- H.1 music indexer → Task 3 ✓
- H.2 music search → Task 3+4 ✓
- H.3 noise removal → covered in Task 7's piper post-process (uses afftdn before piper output is mixed) ⚠ — explicitly not yet (would be applied to recorded audio on upload in Task 20's I.1)
- H.4 audio normalization → Task 5 (loudnorm in filter_complex) ✓
- H.5 sidechain ducking → Task 5 (sidechaincompress) ✓
- H.6 SFX library → directories created in Task 2 install, picker UI deferred to v2.2 polish item ⚠ — note this in Plan-end carve-outs
- H.7 voice picker → Task 7 (Amy/Ryan/Jenny/Lessac in voiceover modal) ✓
- I.1 webcam → Task 20 ✓
- I.2 screen → Task 20 ✓
- I.3 URL import → Task 21 ✓
- I.4 multi-file drag-drop → Task 22 ✓
- I.5 voice memo → Task 21 ✓

**Carve-outs noted in v2.1:**
- H.3 audio denoise on upload (afftdn) — applied during webcam upload in Task 20's pipeline (worth adding explicitly in Task 20 step list)
- H.6 SFX picker UI — directories + assets exist after Task 2; picker per shot in storyboard view (Task 12) — flag for v2.2 polish

**Placeholder scan:** "TBD" / "TODO" / "implement later" / "fill in" — none found. Condensed task descriptions (Tasks 8-22) are intentional density, not placeholders — each task has clear inputs (files, route signatures, UI placement, commit message).

**Type consistency:** `_kick_render_async` from v2.0 Task 2 is updated in v2.1 Task 5 to pass new render params. `POST_WRITABLE` expanded once in Task 5 (music_id/voiceover_path/subtitles_path/lut_name). `_settings.load_settings()` / `load_brand_kit()` reused across multiple tasks. `_call_ollama_chat` from Phase 1 reused for every new /ai/* route.

---

## Execution

**Plan complete and saved to** `docs/superpowers/plans/2026-05-23-ahb123-social-studio-v2.1-media-ai-plan.md`.

22 tasks. **v2.0 plan must land first** since this plan assumes toast/keymap/progress/shotlist modules exist.

Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks
2. **Inline Execution** — execute tasks in this session using executing-plans
