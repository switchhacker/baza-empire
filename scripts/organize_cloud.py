#!/usr/bin/env python3
"""organize_cloud.py — build date-organized symlink trees over /mnt/empirepool/cloud/1/Imports/.

Non-destructive. Creates symlinks in:
  Photos/YYYY/YYYY-MM/           (jpg, heic, png, ...)
  Photos-360/YYYY/YYYY-MM/       (.insp)
  Videos/YYYY/YYYY-MM/           (mp4, mov, m4v, ...)
  Videos-360/YYYY/YYYY-MM/       (.insv, plus .lrv/.thm sidecars)
  Audio/YYYY/YYYY-MM/            (mp3, m4a, ...)
  Documents/                     (pdf, docx, ...)  — flat, no date split

Idempotent: re-running refreshes the trees. Existing symlinks with the wrong
target are replaced; junk symlinks (missing target) are removed.

Ignores Mac/iOS/app cruft (plist, aae, sqlite, thumb*, insv.analyze, etc).
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from pathlib import Path

CLOUD_ROOT = Path("/mnt/empirepool/cloud/1")
IMPORTS = CLOUD_ROOT / "Imports"

PHOTO_EXTS   = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".gif", ".bmp", ".tiff", ".tif",
                ".raw", ".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf", ".rw2"}
PHOTO_360    = {".insp"}
VIDEO_EXTS   = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".3gp", ".wmv", ".flv", ".mts"}
VIDEO_360    = {".insv"}
VIDEO_SIDECAR = {".lrv", ".thm"}          # companion to .insv — park alongside
AUDIO_EXTS   = {".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg", ".opus", ".wma"}
DOC_EXTS     = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt",
                ".rtf", ".odt", ".csv", ".pages", ".numbers", ".key", ".md"}

SKIP_EXTS = {
    ".plist", ".aae", ".sqlite", ".sqlite-wal", ".sqlite-shm",
    ".db", ".kgdb", ".graphdb", ".ivf", ".shadow", ".header", ".buckets",
    ".offsets", ".succ", ".analyze", ".plj", ".data", ".cmap", ".itc2", ".pb",
}
SKIP_NAMES = {".ds_store", ".thumbnails"}
SKIP_SUBSTR = (".thumb440x696", ".thumb696x440", ".insv.analyze",
               ".ithmb")

DATE_REGEXES = [
    re.compile(r"(?P<y>\d{4})[-_]?(?P<m>\d{2})[-_]?(?P<d>\d{2})"),   # 2025-07-01 or 20250701
]


def category_of(ext: str, name_lower: str) -> str | None:
    if any(s in name_lower for s in SKIP_SUBSTR):  return None
    if name_lower in SKIP_NAMES:                   return None
    if ext in SKIP_EXTS:                           return None
    if ext in PHOTO_EXTS:                          return "Photos"
    if ext in PHOTO_360:                           return "Photos-360"
    if ext in VIDEO_EXTS:                          return "Videos"
    if ext in VIDEO_360 or ext in VIDEO_SIDECAR:   return "Videos-360"
    if ext in AUDIO_EXTS:                          return "Audio"
    if ext in DOC_EXTS:                            return "Documents"
    return None  # unknown — leave in Imports/ only


def date_for(src: Path) -> datetime:
    """Best-effort date: filename regex first, then mtime."""
    for rx in DATE_REGEXES:
        m = rx.search(src.name)
        if m:
            try:
                y, mm, d = int(m["y"]), int(m["m"]), int(m["d"])
                if 1990 <= y <= 2100 and 1 <= mm <= 12 and 1 <= d <= 31:
                    return datetime(y, mm, d)
            except ValueError:
                pass
    try:
        return datetime.fromtimestamp(src.stat().st_mtime)
    except OSError:
        return datetime.now()


def target_dir(category: str, date: datetime) -> Path:
    if category == "Documents":
        return CLOUD_ROOT / category
    return CLOUD_ROOT / category / f"{date.year:04d}" / f"{date.year:04d}-{date.month:02d}"


def unique_name(directory: Path, name: str) -> str:
    """If name exists in directory and points elsewhere, add _N suffix."""
    if not (directory / name).exists() and not (directory / name).is_symlink():
        return name
    stem, ext = os.path.splitext(name)
    for i in range(1, 10000):
        cand = f"{stem}_{i}{ext}"
        if not (directory / cand).exists() and not (directory / cand).is_symlink():
            return cand
    raise RuntimeError(f"too many collisions for {name} in {directory}")


def link_one(src: Path, stats: dict) -> None:
    if not src.is_file():                      stats["skip_nonfile"] += 1; return
    ext = src.suffix.lower()
    name_lower = src.name.lower()
    cat = category_of(ext, name_lower)
    if cat is None:                            stats["skip_type"]    += 1; return

    date = date_for(src)
    dst_dir = target_dir(cat, date)
    dst_dir.mkdir(parents=True, exist_ok=True)

    dst = dst_dir / src.name
    try:
        current = os.readlink(dst) if dst.is_symlink() else None
    except OSError:
        current = None

    if current == str(src):
        stats["already_linked"] += 1
        return

    if dst.exists() or dst.is_symlink():
        # existing file or different-target symlink — rename ours
        new_name = unique_name(dst_dir, src.name)
        dst = dst_dir / new_name

    try:
        os.symlink(str(src), str(dst))
        stats["linked"] += 1
    except OSError as e:
        stats["error"] += 1
        print(f"  ERR {src}: {e}", file=sys.stderr)


def prune_dangling(root: Path, stats: dict) -> None:
    """Remove symlinks in organized trees whose target no longer exists."""
    if not root.exists(): return
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            p = Path(dirpath) / name
            if p.is_symlink() and not p.exists():
                try:
                    p.unlink()
                    stats["pruned"] += 1
                except OSError:
                    pass


def main() -> int:
    if not IMPORTS.is_dir():
        print(f"no imports tree at {IMPORTS}", file=sys.stderr)
        return 1

    stats = {"linked": 0, "already_linked": 0, "skip_type": 0, "skip_nonfile": 0,
             "error": 0, "pruned": 0}

    # prune first so re-runs are clean
    for cat in ("Photos", "Photos-360", "Videos", "Videos-360", "Audio", "Documents"):
        prune_dangling(CLOUD_ROOT / cat, stats)

    for dirpath, _, filenames in os.walk(IMPORTS):
        for name in filenames:
            # hidden metadata files of our own — skip
            if name in (".import_manifest.sha256", ".import_meta.txt"):
                continue
            link_one(Path(dirpath) / name, stats)

    print("--- organize summary ---")
    for k in ("linked", "already_linked", "skip_type", "skip_nonfile", "pruned", "error"):
        print(f"  {k:16s} : {stats[k]}")

    # report tree sizes
    print("\n--- organized tree sizes ---")
    for cat in ("Photos", "Photos-360", "Videos", "Videos-360", "Audio", "Documents"):
        root = CLOUD_ROOT / cat
        if not root.exists(): continue
        n = sum(1 for _ in root.rglob("*") if _.is_symlink())
        print(f"  {cat:14s} : {n} links")

    return 0


if __name__ == "__main__":
    sys.exit(main())
