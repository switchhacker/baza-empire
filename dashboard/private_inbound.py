"""Inbound staging + vault privacy gating.

Two distinct concepts that live side-by-side under `dashboard/artifacts/`:

  - **Inbound** (`.private-inbound/<agent_id>/`) — public staging for any file
    Telegram drops onto an agent (photos, PDFs, video, audio, voice). These
    are NOT private. They appear in Data Hub by default. The dir name kept
    its dotted "private-inbound" prefix for filesystem-historical reasons —
    vision.db stores absolute paths under it, so renaming would break the
    catalogue. Treat the name as a legacy label; the semantics are public.

  - **Vault** (`.vault/`) — the only strictly-private location. Files only
    arrive here via an explicit user action ("Send to Vault" from Data Hub).
    Vault files are excluded from Data Hub entirely (no ghost thumbnails)
    and only listed by `/api/datahub/private/list` under a session unlock.

A file is private ONLY if:
  1. It sits under `.vault/`, OR
  2. Its `.meta` sidecar still has `private=true` (legacy — swept at boot
     by `migrate_legacy_inbound_meta()` but the check stays as belt-and-
     suspenders).

Telegram inbound flow:
  - Save under `inbound_dir(framework_dir, agent_id)` (the old
    `private_inbound_dir` alias still works — same path).
  - Call `write_attachment_meta(fpath, extra=...)` to record agent_id,
    chat_id, caption, etc. — but NOT a private flag.

Send-to-vault flow:
  - `move_to_vault(src_path, framework_dir, extra=...)` moves the file into
    `.vault/` and stamps its `.meta` with `private=true`.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from typing import Optional


# Legacy directory name — kept on disk because vision.db stores absolute
# paths inside it. Semantics changed in 2026-05-15: contents are now PUBLIC
# (visible in Data Hub) rather than auto-private.
PRIVATE_INBOUND_DIRNAME = ".private-inbound"

# Canonical strict-private location. Only files explicitly moved here are
# private. Excluded from Data Hub indexing entirely.
VAULT_DIRNAME = ".vault"


def inbound_dir(framework_dir: str, agent_id: str) -> str:
    """Return the public inbound staging dir for an agent and ensure it
    exists. (Disk name still `.private-inbound/` for backwards compat.)"""
    path = os.path.join(framework_dir, "dashboard", "artifacts",
                        PRIVATE_INBOUND_DIRNAME, agent_id)
    os.makedirs(path, exist_ok=True)
    return path


def private_inbound_dir(framework_dir: str, agent_id: str) -> str:
    """DEPRECATED alias. The dir is no longer auto-private; this name is
    kept so existing callers (Sam, base_agent) work without code changes."""
    return inbound_dir(framework_dir, agent_id)


def vault_dir(framework_dir: str) -> str:
    """Return the strict-private vault dir and ensure it exists."""
    path = os.path.join(framework_dir, "dashboard", "artifacts", VAULT_DIRNAME)
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


def _path_under_vault(abs_path: str) -> bool:
    parts = os.path.normpath(abs_path).split(os.sep)
    return VAULT_DIRNAME in parts


def is_private(abs_path: str) -> bool:
    """True if `abs_path` is in the vault, OR has a legacy private meta flag.

    The old `.private-inbound/` path no longer auto-marks files private;
    Data Hub shows them by default."""
    if not abs_path:
        return False
    if _path_under_vault(abs_path):
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


def _write_meta(fpath: str, payload: dict) -> None:
    meta_path = fpath + ".meta"
    try:
        existing = _read_meta(meta_path)
        merged: dict = dict(existing)
        merged.update(payload)
        with open(meta_path, "w", encoding="utf-8") as mf:
            json.dump(merged, mf, indent=2, default=str)
    except OSError:
        pass


def write_attachment_meta(fpath: str, extra: Optional[dict] = None) -> None:
    """Write/merge a `.meta` sidecar with attachment metadata (agent_id,
    chat_id, kind, caption, …) but WITHOUT a private flag. Use for the
    Telegram-inbound flow now that inbound is public."""
    payload = dict(extra) if extra else {}
    # Defensively strip private flags in case a caller leaked one in.
    payload.pop("private", None)
    payload.pop("privacy", None)
    _write_meta(fpath, payload)


def mark_private(fpath: str, extra: Optional[dict] = None) -> None:
    """Stamp `fpath` as private in its `.meta` sidecar. Now only used by
    `move_to_vault()` and any explicit user action; the inbound flow uses
    `write_attachment_meta()` instead."""
    payload = dict(extra) if extra else {}
    payload["private"] = True
    payload["privacy"] = "private"
    _write_meta(fpath, payload)


def move_to_vault(src_path: str, framework_dir: str,
                  extra: Optional[dict] = None) -> str:
    """Move `src_path` into `.vault/` and mark it private. Also moves the
    `.meta` sidecar. Returns the new absolute path inside the vault.

    Idempotent: if already in vault, returns the existing path."""
    src_path = os.path.abspath(src_path)
    if not os.path.isfile(src_path):
        raise FileNotFoundError(src_path)
    if _path_under_vault(src_path):
        if extra:
            mark_private(src_path, extra=extra)
        else:
            mark_private(src_path)
        return src_path
    vault = vault_dir(framework_dir)
    base = os.path.basename(src_path)
    dest = os.path.join(vault, base)
    # Avoid collisions — append uuid suffix.
    if os.path.exists(dest):
        import uuid as _uuid
        stem, ext = os.path.splitext(base)
        dest = os.path.join(vault, f"{stem}_{_uuid.uuid4().hex[:6]}{ext}")
    shutil.move(src_path, dest)
    # Move sidecar if present.
    src_meta = src_path + ".meta"
    if os.path.isfile(src_meta):
        try:
            shutil.move(src_meta, dest + ".meta")
        except OSError:
            pass
    mark_private(dest, extra=extra or {})
    return dest


def move_out_of_vault(src_path: str, framework_dir: str,
                      dest_dir: Optional[str] = None) -> str:
    """Move a vault file back out. Defaults dest to `.private-inbound/manual/`
    (the public inbound staging). Strips the private flag from `.meta`."""
    src_path = os.path.abspath(src_path)
    if not _path_under_vault(src_path):
        raise ValueError("source is not in vault")
    if not os.path.isfile(src_path):
        raise FileNotFoundError(src_path)
    dest_dir = dest_dir or inbound_dir(framework_dir, "manual")
    os.makedirs(dest_dir, exist_ok=True)
    base = os.path.basename(src_path)
    dest = os.path.join(dest_dir, base)
    if os.path.exists(dest):
        import uuid as _uuid
        stem, ext = os.path.splitext(base)
        dest = os.path.join(dest_dir, f"{stem}_{_uuid.uuid4().hex[:6]}{ext}")
    shutil.move(src_path, dest)
    src_meta = src_path + ".meta"
    if os.path.isfile(src_meta):
        try:
            shutil.move(src_meta, dest + ".meta")
        except OSError:
            pass
    # Strip private flag from the moved sidecar.
    meta = _read_meta(dest + ".meta")
    if meta:
        meta.pop("private", None)
        meta.pop("privacy", None)
        try:
            with open(dest + ".meta", "w", encoding="utf-8") as mf:
                json.dump(meta, mf, indent=2, default=str)
        except OSError:
            pass
    return dest


def observe_into_vision(fpath: str, *, agent_id: Optional[str] = None) -> None:
    """Best-effort: register the file with the Vision catalogue. Failures are
    swallowed — never break the upload flow because vision indexing is down."""
    try:
        from dashboard.vision.ingest import observe
        observe(fpath, source="inbound", origin_agent=agent_id)
    except Exception:
        pass


def migrate_legacy_inbound_meta(framework_dir: str) -> int:
    """One-shot sweep: walk `.private-inbound/` and strip `private=true` /
    `privacy=private` from every `.meta` sidecar so legacy files become
    visible in Data Hub. Returns the number of sidecars updated.

    Safe to run repeatedly — idempotent."""
    root = os.path.join(framework_dir, "dashboard", "artifacts",
                        PRIVATE_INBOUND_DIRNAME)
    if not os.path.isdir(root):
        return 0
    swept = 0
    for r, _dirs, files in os.walk(root):
        for fn in files:
            if not fn.endswith(".meta"):
                continue
            mp = os.path.join(r, fn)
            meta = _read_meta(mp)
            if not meta:
                continue
            changed = False
            if "private" in meta:
                meta.pop("private", None); changed = True
            if "privacy" in meta and str(meta["privacy"]).strip().lower() == "private":
                meta.pop("privacy", None); changed = True
            if changed:
                try:
                    with open(mp, "w", encoding="utf-8") as mf:
                        json.dump(meta, mf, indent=2, default=str)
                    swept += 1
                except OSError:
                    pass
    return swept


_INBOUND_FILENAME_RE = re.compile(
    r"^(?:sam_axe|phil_hass|nova_sterling|claw_batto|simon_bately|"
    r"duke_harmon|scout_reeves|rex_valor|specter_voss)_\d{8}_\d{4}_[0-9a-f]+\.[a-z0-9]+$",
    re.IGNORECASE,
)


def is_legacy_inbound_filename(basename: str) -> bool:
    """Heuristic: is this filename a Telegram-inbound photo from before privacy gating?"""
    return bool(_INBOUND_FILENAME_RE.match(basename))
