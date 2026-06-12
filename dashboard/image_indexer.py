#!/usr/bin/env python3
"""
Baza Empire — Data Hub image indexer.

Captions every image under dashboard/artifacts/ using a local vision model
(qwen3-vl via Ollama, llava:13b fallback) and stores captions + tag keywords
in SQLite so the Data Hub live-search can match on image content.

Designed to coexist with running agents:
  • short per-image budget with exponential backoff on GPU contention
  • resumable via (abs_path, mtime) — unchanged files skipped
  • low concurrency (1) — never pins the GPU
  • graceful on 500/resource errors — marks failed, moves on, retries next tick
  • downscales images to 384 px before sending (≈10× smaller payload)

Usage:
  python3 image_indexer.py                 # one pass over new/changed images
  python3 image_indexer.py --force         # re-caption every image
  python3 image_indexer.py --limit 20      # stop after 20 new captions
  python3 image_indexer.py --retry-failed  # retry previously failed
"""
import argparse
import base64
import io
import json
import os
import signal
import sqlite3
import sys
import time
import urllib.error
import urllib.request

try:
    from PIL import Image
except ImportError:
    print("ERROR: Pillow (PIL) required. pip install Pillow", file=sys.stderr)
    sys.exit(2)

DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))
ARTIFACTS_DIR = os.path.join(DASHBOARD_DIR, "artifacts")
DB_PATH       = os.path.join(DASHBOARD_DIR, "image_captions.db")

# Privacy gating — Telegram-inbound media is captured into a `.private-inbound/`
# tree and tagged with private=true in its `.meta` sidecar. The indexer must
# skip these so personal reference photos never end up in the Data Hub search
# index. Falls back gracefully if the helper isn't importable.
try:
    sys.path.insert(0, os.path.dirname(DASHBOARD_DIR))
    from dashboard.private_inbound import is_private as _is_private
except ImportError:
    def _is_private(_p: str) -> bool:  # type: ignore[misc]
        return False

VISION_MODELS = ["qwen3-vl:latest", "llava:13b"]
OLLAMA_PORTS  = [11434]  # AMD RX 6700 XT; NVIDIA (11435) reserved for SD WebUI

IMG_EXTS      = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif"}
MIN_SIZE      = 20 * 1024          # skip tiny images (icons/thumbnails)
MAX_FILE_SIZE = 25 * 1024 * 1024   # skip absurdly huge files
DOWNSCALE_PX  = 384                # max long-side before sending to model

RETRY_FAILED_AFTER = 6 * 3600      # retry 'failed' rows after this many seconds
PER_IMAGE_TIMEOUT  = 90            # seconds per caption call
INTER_IMAGE_SLEEP  = 0.5           # yield cycles between images
BACKOFF_ON_500     = 20            # seconds to wait after GPU contention

# Ask for a two-line structured response. Short prompt = fewer thinking tokens
# on qwen3-vl (which is chatty by default).
CAPTION_PROMPT = (
    "Respond with ONLY two lines, no preamble, no thinking:\n"
    "CAPTION: <one natural-language sentence describing the image>\n"
    "TAGS: <12 comma-separated keywords — objects, room/scene type, colors, "
    "style, materials, activities, any text visible>"
)

_SHUTDOWN = False


def _sigterm(signum, frame):
    global _SHUTDOWN
    _SHUTDOWN = True
    print("[indexer] SIGTERM received — finishing current image and exiting", flush=True)


signal.signal(signal.SIGTERM, _sigterm)
signal.signal(signal.SIGINT,  _sigterm)


# ─── DB ──────────────────────────────────────────────────────────────────────

def db_open() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.execute("""
      CREATE TABLE IF NOT EXISTS image_captions (
        abs_path   TEXT PRIMARY KEY,
        project_id TEXT,
        sub_path   TEXT,
        caption    TEXT,
        tags       TEXT,
        mtime      REAL,
        indexed_at REAL,
        model      TEXT,
        status     TEXT,        -- 'ok' | 'failed' | 'skipped'
        error      TEXT
      );
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_captions_caption ON image_captions(caption);")
    con.execute("CREATE INDEX IF NOT EXISTS idx_captions_tags    ON image_captions(tags);")
    con.execute("CREATE INDEX IF NOT EXISTS idx_captions_status  ON image_captions(status);")
    con.commit()
    return con


def db_get(con: sqlite3.Connection, abs_path: str):
    cur = con.execute(
        "SELECT mtime, status, indexed_at FROM image_captions WHERE abs_path = ?",
        (abs_path,),
    )
    return cur.fetchone()


def db_upsert(con: sqlite3.Connection, **row):
    con.execute("""
      INSERT INTO image_captions
        (abs_path, project_id, sub_path, caption, tags, mtime, indexed_at, model, status, error)
      VALUES
        (:abs_path, :project_id, :sub_path, :caption, :tags, :mtime, :indexed_at, :model, :status, :error)
      ON CONFLICT(abs_path) DO UPDATE SET
        project_id=excluded.project_id, sub_path=excluded.sub_path,
        caption=excluded.caption, tags=excluded.tags,
        mtime=excluded.mtime, indexed_at=excluded.indexed_at,
        model=excluded.model, status=excluded.status, error=excluded.error
    """, row)
    con.commit()


# ─── Filesystem walk ─────────────────────────────────────────────────────────

def walk_images():
    if not os.path.isdir(ARTIFACTS_DIR):
        return
    for root, dirs, fnames in os.walk(ARTIFACTS_DIR):
        # Skip dotted dirs — including .private-inbound/ — outright. Defense
        # in depth: the per-file is_private check below also catches stray
        # private-marked files that happen to live in a public dir.
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fn in fnames:
            ext = os.path.splitext(fn)[1].lower()
            if ext not in IMG_EXTS:
                continue
            if fn.endswith(".meta"):
                continue
            path = os.path.join(root, fn)
            try:
                st = os.stat(path)
            except OSError:
                continue
            if st.st_size < MIN_SIZE or st.st_size > MAX_FILE_SIZE:
                continue
            if _is_private(path):
                continue
            rel = os.path.relpath(path, ARTIFACTS_DIR).replace(os.sep, "/")
            parts = rel.split("/")
            proj = parts[0] if len(parts) > 1 else "shared"
            sub  = "/".join(parts[1:]) if len(parts) > 1 else fn
            yield path, proj, sub, st.st_mtime


# ─── Vision call ─────────────────────────────────────────────────────────────

def downscale_to_b64(path: str, max_px: int = DOWNSCALE_PX) -> str:
    img = Image.open(path).convert("RGB")
    img.thumbnail((max_px, max_px), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80, optimize=True)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def parse_caption(txt: str):
    """Pull CAPTION: and TAGS: lines out of model output."""
    cap, tags = "", ""
    for line in (txt or "").strip().splitlines():
        s = line.strip()
        up = s.upper()
        if up.startswith("CAPTION:"):
            cap = s.split(":", 1)[1].strip()
        elif up.startswith("TAGS:"):
            tags = s.split(":", 1)[1].strip()
    if not cap:
        cap = (txt or "").strip().replace("\n", " ")[:400]
    return cap, tags


class GPUContention(Exception):
    """Raised on Ollama 500 — model couldn't load (agents using VRAM)."""


def ollama_caption(img_b64: str, model: str, port: int) -> str:
    payload = json.dumps({
        "model": model,
        "stream": False,
        "keep_alive": "10m",
        "options": {
            "num_predict": 1500,    # qwen3-vl thinks before responding
            "temperature": 0.2,
            "num_ctx": 3072,
        },
        "messages": [
            {"role": "user", "content": CAPTION_PROMPT, "images": [img_b64]}
        ],
        "think": False,  # Ollama 0.30+: qwen3-vl otherwise spends num_predict in `thinking`, content comes back empty
    }).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=PER_IMAGE_TIMEOUT) as resp:
            data = json.loads(resp.read())
            return (data.get("message") or {}).get("content", "") or ""
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        if e.code == 500 and ("resource" in body.lower() or "failed to load" in body.lower()):
            raise GPUContention(body)
        raise


def caption_image(path: str):
    """Returns (caption, tags, model_id) on success, (None, None, None) otherwise."""
    try:
        b64 = downscale_to_b64(path)
    except Exception as e:
        return None, None, None, f"downscale-failed: {e}"

    last_err = ""
    for model in VISION_MODELS:
        for port in OLLAMA_PORTS:
            try:
                out = ollama_caption(b64, model, port)
                if out.strip():
                    cap, tags = parse_caption(out)
                    if cap:
                        return cap, tags, f"{model}@{port}", ""
            except GPUContention as e:
                last_err = f"gpu-busy: {str(e)[:120]}"
                time.sleep(BACKOFF_ON_500)
            except Exception as e:
                last_err = f"{model}@{port}: {str(e)[:120]}"
    return None, None, None, last_err or "no-response"


# ─── Main loop ───────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="re-caption every image")
    ap.add_argument("--retry-failed", action="store_true",
                    help="retry rows with status='failed' regardless of age")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after N new captions this run (0 = no limit)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    con = db_open()

    t0 = time.time()
    captioned = skipped = failed = 0

    for path, proj, sub, mtime in walk_images():
        if _SHUTDOWN:
            break
        if args.limit and captioned >= args.limit:
            break

        existing = db_get(con, path)
        if existing and not args.force:
            old_mtime, status, indexed_at = existing
            if status == "ok" and old_mtime is not None and abs(old_mtime - mtime) < 1.0:
                skipped += 1
                continue
            if status == "failed" and not args.retry_failed:
                # honor cooldown before re-trying a failed row
                if indexed_at and (time.time() - indexed_at) < RETRY_FAILED_AFTER:
                    skipped += 1
                    continue

        t_img = time.time()
        cap, tags, model_id, err = caption_image(path)
        elapsed = time.time() - t_img

        if cap:
            db_upsert(
                con,
                abs_path=path, project_id=proj, sub_path=sub,
                caption=cap, tags=tags, mtime=mtime,
                indexed_at=time.time(), model=model_id, status="ok", error=None,
            )
            captioned += 1
            if args.verbose:
                print(f"[ok {elapsed:5.1f}s] {proj}/{sub}")
                print(f"          {cap[:140]}")
                if tags:
                    print(f"   tags:  {tags[:140]}")
            else:
                print(f"[ok {elapsed:5.1f}s] {proj}/{sub}  — {cap[:90]}", flush=True)
        else:
            db_upsert(
                con,
                abs_path=path, project_id=proj, sub_path=sub,
                caption=None, tags=None, mtime=mtime,
                indexed_at=time.time(), model=None, status="failed", error=err,
            )
            failed += 1
            print(f"[skip {elapsed:5.1f}s] {proj}/{sub}  — {err}", flush=True)

        time.sleep(INTER_IMAGE_SLEEP)

    total_dt = time.time() - t0
    print(f"\n[indexer] captioned={captioned}  skipped={skipped}  failed={failed}  "
          f"elapsed={total_dt:.1f}s", flush=True)


if __name__ == "__main__":
    main()
