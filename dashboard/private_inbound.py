"""Privacy gating for Telegram-inbound photos and attachments.

Inbound media from Serge's Telegram chats often contains personal references
(faces, wardrobe, location photos). This module is the single source of truth
for "is this file private?" so the image indexer, Data Hub search, and serve
endpoints all agree.

A file is private if ANY of these are true:
  1. Its `.meta` sidecar contains the line `private=true` (text format)
     or the JSON key `"private": true` / `"privacy": "private"`.
  2. Its absolute path passes through a directory whose name starts with
     `.private` (e.g. `dashboard/artifacts/.private-inbound/...`). Dotted
     directories are already excluded from `os.walk` filters in the indexer
     and Data Hub grep, so this is belt-and-suspenders.
  3. A `.private` sentinel file exists in the same directory as the file.

Marking flow (used by agents on inbound):
  - Save the media to the agent's normal artifact path.
  - Call `mark_private(fpath, extra=...)` to write/merge a `.meta` sidecar
    with `private=true` plus the agent's metadata.

Unmark flow (rare, manual):
  - Edit the `.meta` to set `private=false`, or move the file out of a
    `.private*` directory.
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional


PRIVATE_INBOUND_DIRNAME = ".private-inbound"


def private_inbound_dir(framework_dir: str, agent_id: str) -> str:
    """Return the canonical private-inbound directory for an agent and ensure it exists."""
    path = os.path.join(framework_dir, "dashboard", "artifacts",
                        PRIVATE_INBOUND_DIRNAME, agent_id)
    os.makedirs(path, exist_ok=True)
    return path


def _read_meta(meta_path: str) -> dict:
    """Read a `.meta` sidecar in either JSON or `key=value` text format."""
    if not os.path.isfile(meta_path):
        return {}
    try:
        with open(meta_path, "r", encoding="utf-8", errors="replace") as fh:
            raw = fh.read().strip()
    except OSError:
        return {}
    if not raw:
        return {}
    if raw.lstrip().startswith("{"):
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, ValueError):
            pass
    out: dict = {}
    for line in raw.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _path_under_private_dir(abs_path: str) -> bool:
    parts = os.path.normpath(abs_path).split(os.sep)
    return any(p.startswith(".private") for p in parts)


def is_private(abs_path: str) -> bool:
    """True if `abs_path` points to a file marked private by any rule above."""
    if not abs_path:
        return False
    if _path_under_private_dir(abs_path):
        return True
    sentinel = os.path.join(os.path.dirname(abs_path), ".private")
    if os.path.isfile(sentinel):
        return True
    meta = _read_meta(abs_path + ".meta")
    if not meta:
        return False
    val = meta.get("private")
    if isinstance(val, bool) and val:
        return True
    if isinstance(val, str) and val.strip().lower() in {"1", "true", "yes"}:
        return True
    if str(meta.get("privacy", "")).strip().lower() == "private":
        return True
    return False


def mark_private(fpath: str, extra: Optional[dict] = None) -> None:
    """Write/merge a `.meta` sidecar marking `fpath` as private.

    Always writes JSON for new sidecars. If a legacy `key=value` sidecar
    already exists, this re-writes it as JSON with `private=true` merged in.
    Failures are swallowed — privacy marking must never break the upload flow.
    """
    meta_path = fpath + ".meta"
    try:
        existing = _read_meta(meta_path)
        merged: dict = dict(existing)
        if extra:
            merged.update(extra)
        merged["private"] = True
        merged["privacy"] = "private"
        with open(meta_path, "w", encoding="utf-8") as mf:
            json.dump(merged, mf, indent=2, default=str)
    except OSError:
        pass


def observe_into_vision(fpath: str, *, agent_id: Optional[str] = None) -> None:
    """Best-effort: register the file with the Vision catalogue. Failures are
    swallowed — never break the upload flow because vision indexing is down."""
    try:
        from dashboard.vision.ingest import observe
        observe(fpath, source="inbound", origin_agent=agent_id)
    except Exception:
        pass


_INBOUND_FILENAME_RE = re.compile(
    r"^(?:sam_axe|phil_hass|nova_sterling|claw_batto|simon_bately|"
    r"duke_harmon|scout_reeves|rex_valor|specter_voss)_\d{8}_\d{4}_[0-9a-f]+\.[a-z0-9]+$",
    re.IGNORECASE,
)


def is_legacy_inbound_filename(basename: str) -> bool:
    """Heuristic: is this filename a Telegram-inbound photo from before privacy gating?

    Used by the one-time backfill to decide which existing artifacts should be
    retroactively marked private.
    """
    return bool(_INBOUND_FILENAME_RE.match(basename))
