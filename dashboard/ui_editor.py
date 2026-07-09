# dashboard/ui_editor.py — Visual editor overrides store + API.
# Spec: docs/superpowers/specs/2026-07-08-nav-fixes-and-visual-editor-design.md (B2)
# Overrides are cosmetic patches (text/image/style/hide/link/order/attr) applied
# by static/edit.js over live dashboard pages. Separate DB — never touches
# baza_projects.db. Revert = soft-delete (active=0) so history survives.
import json
import os
import sqlite3
import uuid
from contextlib import closing, contextmanager

from flask import Blueprint, jsonify, request

_HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_HERE, "ui_overrides.db")
UPLOAD_DIR = os.path.join(_HERE, "static", "uploads")
ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
KINDS = {"text", "image", "style", "hide", "link", "order", "attr"}

ui_bp = Blueprint("ui_editor", __name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS overrides (
  id INTEGER PRIMARY KEY,
  page TEXT NOT NULL,
  selector TEXT NOT NULL,
  kind TEXT NOT NULL,
  value TEXT NOT NULL,
  fingerprint TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  stale INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_overrides_page ON overrides(page, active);
"""


def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


@contextmanager
def _db():
    """Yield a connection that is both transaction-managed and always closed."""
    with closing(_conn()) as conn:
        with conn as c:
            yield c


def init_db():
    with _db() as c:
        c.executescript(_SCHEMA)
        try:
            c.execute("ALTER TABLE overrides ADD COLUMN stale INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # column already exists


def normalize_page(p):
    """Path key for a page: strip query/hash/trailing slash, ensure leading /."""
    if not isinstance(p, str):
        p = "/"
    p = (p or "/").split("?", 1)[0].split("#", 1)[0].strip()
    if not p.startswith("/"):
        p = "/" + p
    return p.rstrip("/") or "/"


def _row(r):
    d = dict(r)
    for field in ("value", "fingerprint"):
        if d.get(field):
            try:
                d[field] = json.loads(d[field])
            except (TypeError, ValueError):
                pass
    return d


@ui_bp.route("/api/ui/overrides")
def list_overrides():
    page = normalize_page(request.args.get("page"))
    with _db() as c:
        rows = c.execute(
            "SELECT * FROM overrides WHERE page=? AND active=1 ORDER BY id",
            (page,)).fetchall()
    return jsonify({"page": page, "overrides": [_row(r) for r in rows]})


@ui_bp.route("/api/ui/overrides", methods=["POST"])
def save_override():
    b = request.get_json(force=True, silent=True) or {}
    for field in ("page", "selector", "kind"):
        if field in b and not isinstance(b.get(field), str):
            return jsonify({"error": "%s must be a string" % field}), 422
    page = normalize_page(b.get("page"))
    selector = (b.get("selector") or "").strip()
    kind = b.get("kind")
    if not selector or len(selector) > 1000:
        return jsonify({"error": "selector required (max 1000 chars)"}), 422
    if kind not in KINDS:
        return jsonify({"error": "kind must be one of %s" % sorted(KINDS)}), 422
    value = json.dumps(b.get("value"))
    fp = json.dumps(b["fingerprint"]) if "fingerprint" in b else None
    with _db() as c:
        row = c.execute(
            "SELECT id FROM overrides WHERE page=? AND selector=? AND kind=? AND active=1",
            (page, selector, kind)).fetchone()
        if row:
            c.execute(
                "UPDATE overrides SET value=?, fingerprint=COALESCE(?, fingerprint),"
                " stale=0, updated_at=datetime('now') WHERE id=?",
                (value, fp, row["id"]))
            oid = row["id"]
        else:
            oid = c.execute(
                "INSERT INTO overrides(page, selector, kind, value, fingerprint)"
                " VALUES(?,?,?,?,?)",
                (page, selector, kind, value, fp)).lastrowid
    return jsonify({"ok": True, "id": oid})


@ui_bp.route("/api/ui/overrides/<int:oid>/revert", methods=["POST"])
def revert_override(oid):
    with _db() as c:
        n = c.execute(
            "UPDATE overrides SET active=0, updated_at=datetime('now') WHERE id=? AND active=1",
            (oid,)).rowcount
    if not n:
        return jsonify({"error": "no active override %d" % oid}), 404
    return jsonify({"ok": True})


@ui_bp.route("/api/ui/overrides/reset", methods=["POST"])
def reset_overrides():
    b = request.get_json(force=True, silent=True) or {}
    for field in ("page", "selector"):
        if field in b and not isinstance(b.get(field), str):
            return jsonify({"error": "%s must be a string" % field}), 422
    page = normalize_page(b.get("page"))
    selector = (b.get("selector") or "").strip()
    with _db() as c:
        if selector:
            n = c.execute(
                "UPDATE overrides SET active=0, updated_at=datetime('now')"
                " WHERE page=? AND selector=? AND active=1", (page, selector)).rowcount
        else:
            n = c.execute(
                "UPDATE overrides SET active=0, updated_at=datetime('now')"
                " WHERE page=? AND active=1", (page,)).rowcount
    return jsonify({"ok": True, "reverted": n})


@ui_bp.route("/api/ui/overrides/stale-report", methods=["POST"])
def stale_report():
    """Client-side apply engine reports which overrides' selectors no longer
    match anything on the live page. Only the browser can know this."""
    b = request.get_json(force=True, silent=True) or {}
    page = normalize_page(b.get("page"))
    stale_ids = b.get("stale_ids") or []
    ok_ids = b.get("ok_ids") or []
    for ids in (stale_ids, ok_ids):
        if not isinstance(ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
            return jsonify({"error": "stale_ids/ok_ids must be lists of ints"}), 422
    marked = cleared = 0
    with _db() as c:
        if stale_ids:
            q = ",".join("?" * len(stale_ids))
            marked = c.execute(
                "UPDATE overrides SET stale=1 WHERE page=? AND active=1"
                " AND stale=0 AND id IN (%s)" % q, [page] + stale_ids).rowcount
        if ok_ids:
            q = ",".join("?" * len(ok_ids))
            cleared = c.execute(
                "UPDATE overrides SET stale=0 WHERE page=? AND active=1"
                " AND stale=1 AND id IN (%s)" % q, [page] + ok_ids).rowcount
    return jsonify({"ok": True, "marked": marked, "cleared": cleared})


@ui_bp.route("/api/ui/overrides/history")
def override_history():
    page = normalize_page(request.args.get("page"))
    with _db() as c:
        rows = c.execute(
            "SELECT * FROM overrides WHERE page=? ORDER BY updated_at DESC, id DESC",
            (page,)).fetchall()
    return jsonify({"page": page, "overrides": [_row(r) for r in rows]})


@ui_bp.route("/api/ui/overrides/summary")
def override_summary():
    with _db() as c:
        rows = c.execute(
            "SELECT page, COUNT(*) AS n,"
            " SUM(CASE WHEN stale=1 THEN 1 ELSE 0 END) AS s"
            " FROM overrides WHERE active=1 GROUP BY page ORDER BY page").fetchall()
    return jsonify({"pages": [
        {"page": r["page"], "count": r["n"], "stale": r["s"] or 0} for r in rows]})


@ui_bp.route("/api/ui/upload", methods=["POST"])
def upload_image():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "no file"}), 422
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ALLOWED_EXT:
        return jsonify({"error": "extension %s not allowed" % ext}), 422
    if request.content_length and request.content_length > MAX_UPLOAD_BYTES:
        return jsonify({"error": "file too large (max %dMB)" % (MAX_UPLOAD_BYTES // 1048576)}), 422
    blob = f.read(MAX_UPLOAD_BYTES + 1)
    if len(blob) > MAX_UPLOAD_BYTES:
        return jsonify({"error": "file too large (max %dMB)" % (MAX_UPLOAD_BYTES // 1048576)}), 422
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    name = uuid.uuid4().hex[:12] + ext
    with open(os.path.join(UPLOAD_DIR, name), "wb") as out:
        out.write(blob)
    return jsonify({"ok": True, "url": "/static/uploads/" + name})
