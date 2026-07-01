# dashboard/bin_store.py
"""Baza Bin — single owner of the file bin.

Files land under BIN_DIR (default /mnt/empirepool/bin, off the git tree) and are
indexed by an isolated SQLite DB (default dashboard/bin.db). Everything that
touches the bin goes through this module so the on-disk layout and the security
guard have exactly one implementation.
"""
import os
import re
import uuid
import base64
import shutil
import sqlite3
import datetime as _dt

_HERE = os.path.dirname(os.path.abspath(__file__))

BIN_DIR = os.path.realpath(os.environ.get("BAZA_BIN_DIR", "/mnt/empirepool/bin"))

_SANITIZE = re.compile(r"[^\w.\-_ ()]")

_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".bmp", ".tiff", ".svg"}
_VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}
_AUDIO_EXT = {".mp3", ".ogg", ".oga", ".opus", ".wav", ".m4a", ".flac", ".aac"}
_DOC_EXT = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt",
            ".csv", ".md", ".rtf", ".zip", ".json", ".xml"}


def bin_db_path() -> str:
    return os.environ.get("BAZA_BIN_DB") or os.path.join(_HERE, "bin.db")


def _bin_db() -> sqlite3.Connection:
    conn = sqlite3.connect(bin_db_path(), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_bin_db() -> None:
    os.makedirs(BIN_DIR, exist_ok=True)
    conn = _bin_db()
    try:
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        except Exception:
            pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bin_files (
                id TEXT PRIMARY KEY,
                name TEXT,
                stored_path TEXT,
                size INTEGER,
                mime_type TEXT,
                kind TEXT,
                caption TEXT,
                source TEXT,
                tg_user_id TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
    finally:
        conn.close()


def classify_kind(name: str, mime: str | None) -> str:
    ext = os.path.splitext(name or "")[1].lower()
    if ext in _IMAGE_EXT:
        return "image"
    if ext in _VIDEO_EXT:
        return "video"
    if ext in _AUDIO_EXT:
        return "audio"
    if ext in _DOC_EXT:
        return "document"
    m = (mime or "").lower()
    if m.startswith("image/"):
        return "image"
    if m.startswith("video/"):
        return "video"
    if m.startswith("audio/"):
        return "audio"
    if m.startswith(("application/", "text/")):
        return "document"
    return "other"


def _row_to_item(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


def add_file(*, filename, src_path=None, data=None, mime_type=None,
             caption="", source="telegram", tg_user_id=None) -> dict:
    os.makedirs(BIN_DIR, exist_ok=True)
    safe = (_SANITIZE.sub("_", filename).strip() or "upload")[:200]
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    stored_name = f"{ts}_{safe}"
    stored_path = os.path.join(BIN_DIR, stored_name)
    if data is not None:
        with open(stored_path, "wb") as fh:
            fh.write(data)
    elif src_path:
        shutil.copy2(src_path, stored_path)     # copy: leave the caller's file alone
    else:
        raise ValueError("add_file requires src_path or data")
    size = os.path.getsize(stored_path)
    kind = classify_kind(filename, mime_type)
    item_id = uuid.uuid4().hex
    conn = _bin_db()
    try:
        conn.execute(
            """INSERT INTO bin_files
               (id, name, stored_path, size, mime_type, kind, caption, source, tg_user_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (item_id, filename, stored_path, size, mime_type or "", kind,
             (caption or "")[:500], source, str(tg_user_id) if tg_user_id else ""))
        conn.commit()
    finally:
        conn.close()
    return get(item_id)


def list_items(*, q=None, kind=None, limit=100, offset=0) -> list[dict]:
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    sql = "SELECT * FROM bin_files"
    where, args = [], []
    if kind:
        where.append("kind = ?"); args.append(kind)
    if q:
        where.append("(name LIKE ? OR caption LIKE ?)")
        args.extend([f"%{q}%", f"%{q}%"])
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?"
    args.extend([limit, offset])
    conn = _bin_db()
    try:
        rows = conn.execute(sql, args).fetchall()
    finally:
        conn.close()
    return [_row_to_item(r) for r in rows]


def get(item_id: str) -> dict | None:
    conn = _bin_db()
    try:
        row = conn.execute("SELECT * FROM bin_files WHERE id=?", (item_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_item(row) if row else None


def bin_token(stored_path_or_item) -> str:
    if isinstance(stored_path_or_item, dict):
        stored_path = stored_path_or_item["stored_path"]
    else:
        stored_path = stored_path_or_item
    rel = os.path.basename(stored_path)
    return "~" + base64.urlsafe_b64encode(rel.encode("utf-8")).decode("ascii").rstrip("=")


def resolve_token(token: str) -> str | None:
    """Resolve a '~'-prefixed bin token to an absolute path inside BIN_DIR.
    Returns None for anything not ours, out-of-tree, or missing. Mirrors the
    hardening in app.py::_pick_decode_token."""
    if not token or not isinstance(token, str) or not token.startswith("~"):
        return None
    raw = token[1:]
    try:
        pad = "=" * (-len(raw) % 4)
        rel = base64.urlsafe_b64decode((raw + pad).encode("ascii")).decode("utf-8")
    except Exception:
        return None
    parts = rel.replace("\\", "/").split("/")
    if ".." in parts or "" in parts and len(parts) > 1:
        return None
    if "/" in rel or "\\" in rel:      # bin is flat — no subpaths allowed
        return None
    if "\x00" in rel:
        return None
    fpath = os.path.realpath(os.path.join(BIN_DIR, rel))
    if not fpath.startswith(BIN_DIR + os.sep) and fpath != BIN_DIR:
        return None
    if not os.path.isfile(fpath):
        return None
    return fpath


def to_public(item: dict) -> dict:
    out = dict(item)
    out["token"] = bin_token(item["stored_path"])
    return out


def copy_to(item_id: str, dest_path: str) -> str:
    item = get(item_id)
    if not item:
        raise KeyError(item_id)
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    shutil.copy2(item["stored_path"], dest_path)   # copy: bin keeps its copy
    return dest_path


def delete(item_id: str) -> bool:
    item = get(item_id)
    if not item:
        return False
    try:
        if os.path.isfile(item["stored_path"]):
            os.remove(item["stored_path"])
    except OSError:
        pass
    conn = _bin_db()
    try:
        conn.execute("DELETE FROM bin_files WHERE id=?", (item_id,))
        conn.commit()
    finally:
        conn.close()
    return True
