"""Universal Share service — Link / Email / Telegram for any non-private file.

Mounts /api/share. Generalizes the cloud_shares token table (cloud-only) to
multiple file roots via the `root` column. Email reuses email_studio's Gmail
send; Telegram reuses Phil's bot.
"""
from __future__ import annotations

import datetime as _dt
import os
import secrets
import sqlite3
from typing import Optional

from flask import Blueprint, jsonify, request

DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH_DEFAULT = os.path.join(DASHBOARD_DIR, "baza_projects.db")
ARTIFACTS_DIR = os.path.join(DASHBOARD_DIR, "artifacts")
CLOUD_STORAGE = os.environ.get("BAZA_CLOUD_STORAGE", "/mnt/empirepool/cloud")
FAMILY_USER_ID = int(os.environ.get("BAZA_FAMILY_USER_ID", "1"))
_DENY_DIRS = (".private-inbound", ".vault_meta")
_MAX_ATTACH_BYTES = 25 * 1024 * 1024

# Logical file roots. resolve_source maps a UI "source" to one of these.
ROOTS = {
    "cloud": os.path.join(CLOUD_STORAGE, str(FAMILY_USER_ID)),
    "artifact": ARTIFACTS_DIR,
}
# UI source name -> root key
_SOURCE_ROOT = {"cloud": "cloud", "artifact": "artifact", "datahub": "artifact"}

share_bp = Blueprint("share", __name__)


def _db_path() -> str:
    return os.environ.get("BAZA_DASHBOARD_DB", DB_PATH_DEFAULT)


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(_db_path(), timeout=5)
    con.row_factory = sqlite3.Row
    return con


def resolve_share_path(root: str, rel: str) -> Optional[str]:
    """Resolve (root, rel) to an absolute path inside the root, or None if
    invalid (traversal, private dir, missing, or unknown root)."""
    base = ROOTS.get(root)
    if not base:
        return None
    base_real = os.path.realpath(base)
    full = os.path.realpath(os.path.join(base_real, rel or ""))
    if not (full == base_real or full.startswith(base_real + os.sep)):
        return None
    if any(seg in _DENY_DIRS for seg in full.split(os.sep)):
        return None
    if not os.path.isfile(full):
        return None
    return full


def resolve_source(source: str, id: str) -> Optional[str]:
    """Map a UI source descriptor to an absolute path (guarded)."""
    root = _SOURCE_ROOT.get(source)
    if not root:
        return None
    return resolve_share_path(root, id)


def _ensure_share_schema():
    """Create cloud_shares if missing and ensure the `root` column exists."""
    con = _conn()
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS cloud_shares (
                token TEXT PRIMARY KEY,
                user_id TEXT NOT NULL DEFAULT '1',
                path TEXT NOT NULL,
                expires_at TEXT,
                created_by TEXT DEFAULT 'serge',
                created_at TEXT DEFAULT (datetime('now')),
                access_count INTEGER DEFAULT 0,
                last_accessed_at TEXT,
                root TEXT DEFAULT 'cloud'
            )
        """)
        cols = [r[1] for r in con.execute("PRAGMA table_info(cloud_shares)").fetchall()]
        if "root" not in cols:
            con.execute("ALTER TABLE cloud_shares ADD COLUMN root TEXT DEFAULT 'cloud'")
        con.commit()
    finally:
        con.close()


def _public_base_url() -> str:
    env = os.environ.get("BAZA_PUBLIC_URL", "").rstrip("/")
    if env:
        return env
    try:
        return request.host_url.rstrip("/")
    except Exception:
        return ""


def create_link(source_or_root: str, rel: str, days: int = 7) -> dict:
    """Mint a share token for (root, rel). Accepts a UI source or a root key."""
    root = _SOURCE_ROOT.get(source_or_root, source_or_root)
    if root not in ROOTS:
        raise ValueError("unknown source")
    if resolve_share_path(root, rel) is None:
        raise ValueError("file not found or not shareable")
    token = secrets.token_urlsafe(18)
    expires_at = ((_dt.datetime.utcnow() + _dt.timedelta(days=days)).isoformat()
                  if days and days > 0 else None)
    _ensure_share_schema()
    con = _conn()
    try:
        con.execute(
            "INSERT INTO cloud_shares (token, user_id, path, expires_at, created_by, root) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (token, str(FAMILY_USER_ID), rel, expires_at, "serge", root),
        )
        con.commit()
    finally:
        con.close()
    return {"token": token, "url": f"{_public_base_url()}/s/{token}", "expires_at": expires_at}


def share_email(source: str, id: str, to: str, subject: str = "", note: str = "") -> dict:
    """Email a shared file. Attaches the file if <= 25 MB, else sends a link."""
    try:
        import email_studio as es
    except ImportError:
        from dashboard import email_studio as es  # type: ignore
    abs_path = resolve_source(source, id)
    if not abs_path:
        return {"ok": False, "error": "file not found or not shareable"}
    if not (to or "").strip():
        return {"ok": False, "error": "missing 'to' address"}
    fname = os.path.basename(abs_path)
    subject = (subject or f"Shared: {fname}").strip()
    note = note or ""
    acc = es._active_account()
    if not acc:
        return {"ok": False, "error": "No Gmail account configured"}
    from_addr = acc["email"]
    size = os.path.getsize(abs_path)
    via = "attachment"
    if size > _MAX_ATTACH_BYTES:
        via = "link"
        link = create_link(source, id, days=7)
        body = (note + "\n\n" if note else "") + \
               f"{fname} is too large to attach ({size // (1024*1024)} MB). " \
               f"Download it here:\n{link['url']}\n"
        # The share URL belongs in the body only — never in the Subject header.
        raw = es._mime_message(to, subject, body, from_addr=from_addr)
    else:
        import mimetypes
        with open(abs_path, "rb") as f:
            data = f.read()
        mime = mimetypes.guess_type(fname)[0] or "application/octet-stream"
        body = (note + "\n\n" if note else "") + f"Sharing {fname} (attached)."
        raw = es._mime_message(to, subject, body, from_addr=from_addr,
                               attachments=[{"filename": fname, "mimetype": mime, "data": data}])
    svc = es._gmail(acc["id"])
    svc.users().messages().send(userId="me", body={"raw": raw}).execute()
    return {"ok": True, "via": via, "filename": fname}


def share_telegram(source: str, id: str, chat_id: str = "", caption: str = "") -> dict:
    """Send a shared file to Telegram via Phil's bot (file-type aware)."""
    import requests
    abs_path = resolve_source(source, id)
    if not abs_path:
        return {"ok": False, "error": "file not found or not shareable"}
    token = os.environ.get("CLOUD_TELEGRAM_BOT") or os.environ.get("TELEGRAM_PHIL_HASS")
    if not token:
        return {"ok": False, "error": "No Telegram bot token (set TELEGRAM_PHIL_HASS)"}
    chat_id = str(chat_id or os.environ.get("SERGE_CHAT_ID") or "").strip()
    if not chat_id:
        return {"ok": False, "error": "No chat_id and SERGE_CHAT_ID not set"}
    ext = os.path.splitext(abs_path)[1].lower()
    if ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        method, field = "sendPhoto", "photo"
    elif ext in (".mp4", ".mov", ".m4v", ".webm"):
        method, field = "sendVideo", "video"
    elif ext in (".mp3", ".m4a", ".wav", ".ogg"):
        method, field = "sendAudio", "audio"
    else:
        method, field = "sendDocument", "document"
    try:
        with open(abs_path, "rb") as fh:
            files = {field: (os.path.basename(abs_path), fh)}
            payload = {"chat_id": chat_id}
            if caption:
                payload["caption"] = caption[:1024]
            resp = requests.post(f"https://api.telegram.org/bot{token}/{method}",
                                 data=payload, files=files, timeout=120)
        try:
            result = resp.json()
        except Exception:
            result = {}
        if resp.status_code == 200 and result.get("ok"):
            return {"ok": True, "method": method, "chat_id": chat_id,
                    "filename": os.path.basename(abs_path)}
        return {"ok": False, "error": result.get("description") or "telegram send failed"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@share_bp.route("/api/share", methods=["POST"])
def api_share():
    """Dispatch a share. Body: {source, id, channel, ...channel_args}.
      channel=link:     {expires_days?}             -> {ok, token, url, expires_at}
      channel=email:    {to, subject?, note?}       -> {ok, via, filename}
      channel=telegram: {chat_id?, caption?}        -> {ok, method, ...}
    """
    data = request.get_json(silent=True) or {}
    source = data.get("source", "")
    id = data.get("id", "")
    channel = data.get("channel", "")
    if resolve_source(source, id) is None:
        return jsonify({"ok": False, "error": "file not found or not shareable"}), 403
    if channel == "link":
        try:
            out = create_link(source, id, days=int(data.get("expires_days", 7)))
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify({"ok": True, **out})
    if channel == "email":
        out = share_email(source, id, data.get("to", ""), data.get("subject", ""), data.get("note", ""))
        return (jsonify(out), 200) if out.get("ok") else (jsonify(out), 400)
    if channel == "telegram":
        out = share_telegram(source, id, str(data.get("chat_id", "")), data.get("caption", ""))
        return (jsonify(out), 200) if out.get("ok") else (jsonify(out), 400)
    return jsonify({"ok": False, "error": f"unknown channel: {channel}"}), 400
