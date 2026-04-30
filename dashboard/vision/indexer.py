"""Vision indexer — consume pending assets, classify, persist attributes.

Mirrors image_indexer.py's behavior: low-priority, resumable, retries failed
rows after a cooldown, never pins the GPU.
"""
from __future__ import annotations

import argparse
import signal
import sys
import time
from typing import Optional

from dashboard.vision.classifier import (
    ClassifierError, GPUContention, classify,
)
from dashboard.vision.db import DEFAULT_DB_PATH, connect, init_db

RETRY_FAILED_AFTER = 6 * 3600
INTER_IMAGE_SLEEP = 0.5
BACKOFF_ON_500 = 20

# v1 focus: people only. InsightFace runs as a fast pre-filter before the
# expensive qwen3-vl classification call — images with no detectable face
# are marked rejected and never hit the GPU. Set False to also catalogue
# scenes/objects/etc once we have person coverage.
PEOPLE_ONLY = True

_SHUTDOWN = False


def _sigterm(_sig, _frame):
    global _SHUTDOWN
    _SHUTDOWN = True
    print("[vision-indexer] SIGTERM — finishing current image and exiting", flush=True)


signal.signal(signal.SIGTERM, _sigterm)
signal.signal(signal.SIGINT, _sigterm)


def _attrs_blob(attrs: dict) -> str:
    """Compose the FTS5 attrs_blob: `key:value key:value ...`."""
    skip = {"caption", "tags"}
    return " ".join(f"{k}:{v}" for k, v in attrs.items() if k not in skip and v)


def _persist(con, asset_id: int, attrs: dict, model: str) -> None:
    """Write attributes + caption rows + sync FTS5. Single transaction."""
    caption = attrs.get("caption", "")
    tags = attrs.get("tags", "")
    blob = _attrs_blob(attrs)

    con.execute("BEGIN")
    try:
        for k, v in attrs.items():
            if k in ("caption", "tags") or v == "" or v is None:
                continue
            con.execute(
                """INSERT INTO attributes (asset_id, key, value, confidence, source)
                   VALUES (?, ?, ?, 1.0, ?)
                   ON CONFLICT (asset_id, key) DO UPDATE SET
                       value=excluded.value,
                       confidence=excluded.confidence,
                       source=excluded.source""",
                (asset_id, k, v, model),
            )
        con.execute(
            """INSERT INTO captions (asset_id, caption, tags, model)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(asset_id) DO UPDATE SET
                   caption=excluded.caption, tags=excluded.tags, model=excluded.model""",
            (asset_id, caption, tags, model),
        )
        # Re-sync FTS5: delete old rowid then insert.
        con.execute("DELETE FROM assets_fts WHERE rowid = ?", (asset_id,))
        con.execute(
            "INSERT INTO assets_fts (rowid, caption, tags, attrs_blob) VALUES (?, ?, ?, ?)",
            (asset_id, caption, tags, blob),
        )
        con.execute(
            "UPDATE assets SET status='ok', classified_at=?, error=NULL WHERE id=?",
            (time.time(), asset_id),
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def run(db_path: Optional[str] = None, *, force: bool = False,
        retry_failed: bool = False, limit: int = 0, verbose: bool = False) -> int:
    init_db(db_path)
    con = connect(db_path)
    t0 = time.time()
    processed = failed = 0

    cur = con.execute(
        """SELECT id, abs_path, status, classified_at FROM assets
           WHERE status = 'pending'
              OR (status='failed' AND (? OR (?-COALESCE(classified_at,0)) > ?))
              OR (? AND status='ok')
           ORDER BY id ASC""",
        (1 if retry_failed else 0, time.time(), RETRY_FAILED_AFTER, 1 if force else 0),
    )
    rows = cur.fetchall()

    for row in rows:
        if _SHUTDOWN:
            break
        if limit and processed >= limit:
            break

        asset_id = row["id"]
        path = row["abs_path"]
        t_img = time.time()

        # People-only filter: cheap face pre-check before paying for a full
        # qwen3-vl classification. ~100ms vs ~20s. Saves the GPU when
        # private inbound is full of receipts/scenes/objects.
        if PEOPLE_ONLY:
            try:
                from dashboard.vision.cropper import count_faces
                if count_faces(path) == 0:
                    con.execute(
                        "UPDATE assets SET status='rejected', classified_at=?, error=? WHERE id=?",
                        (time.time(), "no faces detected (people-only filter)", asset_id),
                    )
                    con.execute(
                        "INSERT INTO ingest_log (asset_id, step, ok, ts, detail) VALUES (?, 'classify', 0, ?, ?)",
                        (asset_id, time.time(), "people-only-skip"),
                    )
                    elapsed_skip = time.time() - t_img
                    print(f"[skip {elapsed_skip:5.2f}s] {path} — no face", flush=True)
                    continue
            except Exception as fe:
                # Face detector borked — don't block the pipeline; fall through to qwen3-vl.
                print(f"[face-detect-fail] {path}: {fe}", flush=True)

        try:
            attrs, model = classify(path)
        except GPUContention as e:
            print(f"[gpu-busy] sleeping {BACKOFF_ON_500}s — {str(e)[:80]}", flush=True)
            time.sleep(BACKOFF_ON_500)
            continue
        except (ClassifierError, ValueError, OSError) as e:
            con.execute(
                "UPDATE assets SET status='failed', classified_at=?, error=? WHERE id=?",
                (time.time(), str(e)[:300], asset_id),
            )
            con.execute(
                "INSERT INTO ingest_log (asset_id, step, ok, ts, detail) VALUES (?, 'classify', 0, ?, ?)",
                (asset_id, time.time(), str(e)[:300]),
            )
            failed += 1
            print(f"[fail] {path} — {e}", flush=True)
            continue

        try:
            _persist(con, asset_id, attrs, model)
            # Crop pass — only for person-class images with face visible.
            if attrs.get("image_type") == "person" and "face" in (attrs.get("parts_visible") or ""):
                try:
                    from dashboard.vision.cropper import crop_one
                    n = crop_one(path, asset_id, db_path=db_path)
                    if verbose and n:
                        print(f"          + {n} crop(s)", flush=True)
                except Exception as ce:
                    print(f"[crop-fail] {path}: {ce}", flush=True)
                    con.execute(
                        "INSERT INTO ingest_log (asset_id, step, ok, ts, detail) VALUES (?, 'crop', 0, ?, ?)",
                        (asset_id, time.time(), str(ce)[:300]),
                    )
            processed += 1
            elapsed = time.time() - t_img
            if verbose:
                print(f"[ok {elapsed:5.1f}s] {path}\n          {attrs.get('caption','')[:140]}", flush=True)
            else:
                print(f"[ok {elapsed:5.1f}s] {path} — {attrs.get('caption','')[:80]}", flush=True)
        except Exception as e:
            failed += 1
            print(f"[persist-fail] {path}: {e}", flush=True)

        time.sleep(INTER_IMAGE_SLEEP)

    print(f"\n[vision-indexer] processed={processed} failed={failed} elapsed={time.time()-t0:.1f}s", flush=True)
    return 0
