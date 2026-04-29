#!/usr/bin/env python3
"""register_import.py — walk an Imports/<date-label>/ dir and insert rows
into baza_cloud_files so the cloud UI and media indexer see the files.

Usage: register_import.py <absolute_path_under_cloud>
"""
from __future__ import annotations

import mimetypes
import os
import sqlite3
import sys
import uuid
from pathlib import Path

DB = "/home/switchhacker/baza-empire/agent-framework-v3/dashboard/baza_projects.db"
CLOUD_ROOT = "/mnt/empirepool/cloud/1"
USER_ID = "1"

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".gif", ".bmp", ".tiff", ".tif",
              ".raw", ".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf", ".rw2",
              ".insp"}  # Insta360 still
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".3gp", ".wmv", ".flv", ".mts",
              ".insv", ".lrv", ".thm"}  # Insta360, GoPro low-res, camera thumbnail
AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg", ".opus", ".wma"}
DOC_EXTS   = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".rtf", ".odt", ".csv",
              ".pages", ".numbers", ".key", ".md"}

def categorize(ext: str) -> str:
    e = ext.lower()
    if e in IMAGE_EXTS: return "photos"
    if e in VIDEO_EXTS: return "videos"
    if e in AUDIO_EXTS: return "audio"
    if e in DOC_EXTS:   return "documents"
    return "files"

def main() -> int:
    if len(sys.argv) != 2:
        print("usage: register_import.py <abs_path_under_cloud>", file=sys.stderr)
        return 2
    root = Path(sys.argv[1]).resolve()
    if not str(root).startswith(CLOUD_ROOT + "/"):
        print(f"error: path must be under {CLOUD_ROOT}", file=sys.stderr)
        return 2
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 2

    con = sqlite3.connect(DB, timeout=30)
    cur = con.cursor()

    inserted = skipped = 0
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            if name in {".import_manifest.sha256", ".import_meta.txt"}:
                continue
            full = Path(dirpath) / name
            try:
                size = full.stat().st_size
            except OSError:
                continue
            rel = str(full.relative_to(CLOUD_ROOT))
            ext = full.suffix
            category = categorize(ext)
            mime, _ = mimetypes.guess_type(str(full))
            mime = mime or "application/octet-stream"

            cur.execute(
                "SELECT 1 FROM baza_cloud_files WHERE user_id=? AND path=? LIMIT 1",
                (USER_ID, rel),
            )
            if cur.fetchone():
                skipped += 1
                continue
            cur.execute(
                "INSERT INTO baza_cloud_files (id,user_id,filename,path,size,mime_type,category) VALUES (?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), USER_ID, name, rel, size, mime, category),
            )
            inserted += 1

    con.commit()
    con.close()
    print(f"registered: {inserted} new, {skipped} already present")
    return 0

if __name__ == "__main__":
    sys.exit(main())
