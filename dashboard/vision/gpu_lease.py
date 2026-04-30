"""GPU lease — coordinates SD WebUI use across Specter and other cron jobs.

Single row per GPU. Acquire = INSERT-or-replace IF the existing row's expires_at
is in the past. Release = DELETE if you are the holder. Other cron jobs that
use the GPU should call acquire() at start and release() at end (best-effort).
"""
from __future__ import annotations

import time
from typing import Optional

from dashboard.vision.db import connect


def acquire(gpu: str, holder_name: str, ttl: int, *, db_path: Optional[str] = None,
            purpose: Optional[str] = None) -> bool:
    """Try to take the lease. Returns True on success, False if held by other."""
    now = time.time()
    expires = now + ttl
    con = connect(db_path)
    try:
        # If existing row is unexpired and not us, refuse.
        existing = con.execute("SELECT holder, expires_at FROM gpu_lease WHERE gpu=?", (gpu,)).fetchone()
        if existing and existing["expires_at"] > now and existing["holder"] != holder_name:
            return False
        con.execute(
            """INSERT INTO gpu_lease (gpu, holder, acquired_at, expires_at, purpose)
                VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(gpu) DO UPDATE SET
                   holder=excluded.holder, acquired_at=excluded.acquired_at,
                   expires_at=excluded.expires_at, purpose=excluded.purpose""",
            (gpu, holder_name, now, expires, purpose),
        )
        return True
    finally:
        con.close()


def release(gpu: str, holder_name: str, *, db_path: Optional[str] = None) -> None:
    con = connect(db_path)
    try:
        con.execute("DELETE FROM gpu_lease WHERE gpu=? AND holder=?", (gpu, holder_name))
    finally:
        con.close()


def holder(gpu: str, *, db_path: Optional[str] = None) -> Optional[str]:
    con = connect(db_path)
    try:
        row = con.execute(
            "SELECT holder, expires_at FROM gpu_lease WHERE gpu=?", (gpu,)
        ).fetchone()
        if not row:
            return None
        if row["expires_at"] < time.time():
            return None
        return row["holder"]
    finally:
        con.close()
