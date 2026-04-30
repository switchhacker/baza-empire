"""Observe a file on disk into the vision catalogue as a pending asset row."""
from __future__ import annotations

import hashlib
import os
import time
from typing import Optional

from PIL import Image

from dashboard.vision.db import connect


def _sha256(path: str, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            buf = fh.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def _dimensions(path: str) -> tuple[int, int]:
    try:
        with Image.open(path) as img:
            return img.width, img.height
    except Exception:
        return 0, 0


def observe(
    abs_path: str,
    source: str,
    *,
    db_path: Optional[str] = None,
    origin_agent: Optional[str] = None,
    origin_url: Optional[str] = None,
    parent_id: Optional[int] = None,
) -> int:
    """Insert (or fetch existing) the asset row for abs_path. Returns id."""
    abs_path = os.path.abspath(abs_path)
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(abs_path)
    if source not in ("inbound", "scraped", "generated", "crop"):
        raise ValueError(f"bad source: {source}")

    con = connect(db_path)
    try:
        existing = con.execute(
            "SELECT id FROM assets WHERE abs_path = ?", (abs_path,),
        ).fetchone()
        if existing:
            return existing["id"]

        st = os.stat(abs_path)
        w, h = _dimensions(abs_path)
        cur = con.execute(
            """INSERT INTO assets
                (abs_path, source, origin_agent, origin_url, parent_id,
                 width, height, bytes, sha256, mtime, created_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (abs_path, source, origin_agent, origin_url, parent_id,
             w, h, st.st_size, _sha256(abs_path), st.st_mtime, time.time()),
        )
        asset_id = cur.lastrowid
        con.execute(
            "INSERT INTO ingest_log (asset_id, step, ok, ts, detail) VALUES (?, 'ingest', 1, ?, ?)",
            (asset_id, time.time(), source),
        )
        return asset_id
    finally:
        con.close()
