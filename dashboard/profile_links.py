"""AHB123 public profile-link directory ("Find us on …").

Pure local CRUD: an editable list of public profile URLs (LinkedIn, Thumbtack,
HomeAdvisor/Angi, Google Business, socials, website). No LLM, no cloud, no
network. Distinct from social_connections (OAuth publishing credentials).
"""
from __future__ import annotations

import os
import sqlite3
from urllib.parse import urlparse

from flask import Blueprint, jsonify, request

DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))
profile_bp = Blueprint("profile_links", __name__)


def _db_path() -> str:
    return os.environ.get(
        "BAZA_DASHBOARD_DB", os.path.join(DASHBOARD_DIR, "baza_projects.db"))


def _db():
    con = sqlite3.connect(_db_path(), timeout=8.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 8000")
    return con


def _ensure_tables(db_path=None) -> None:
    con = None
    try:
        con = sqlite3.connect(db_path or _db_path(), timeout=8.0)
        con.execute("PRAGMA busy_timeout = 8000")
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS ahb_profile_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL,
                label TEXT,
                url TEXT NOT NULL,
                icon TEXT,
                display_order INTEGER DEFAULT 100,
                visible INTEGER DEFAULT 1,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        con.commit()
    finally:
        if con is not None:
            con.close()


def _normalize_url(raw: str) -> str:
    """Return a safe http(s) URL or raise ValueError (XSS guard for public links)."""
    u = (raw or "").strip()
    if not u:
        raise ValueError("url required")
    parsed = urlparse(u)
    if parsed.scheme:
        if parsed.scheme.lower() not in ("http", "https"):
            raise ValueError("only http(s) URLs allowed")
        return u
    return "https://" + u


@profile_bp.route("/api/ahb/profile-links", methods=["GET"])
def links_list():
    con = _db()
    try:
        rows = con.execute(
            "SELECT * FROM ahb_profile_links ORDER BY display_order, id").fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        con.close()
    return jsonify({"items": [dict(r) for r in rows]})


@profile_bp.route("/api/ahb/profile-links", methods=["POST"])
def links_create():
    d = request.get_json(silent=True) or {}
    platform = (d.get("platform") or "").strip()
    if not platform:
        return jsonify({"error": "platform required"}), 400
    try:
        url = _normalize_url(d.get("url"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    con = _db()
    try:
        cur = con.execute(
            "INSERT INTO ahb_profile_links (platform, label, url, icon, "
            "display_order, visible) VALUES (?,?,?,?,?,?)",
            (platform, d.get("label") or platform, url, d.get("icon"),
             int(d.get("display_order", 100) or 100),
             1 if d.get("visible", True) else 0))
        con.commit()
        row = con.execute("SELECT * FROM ahb_profile_links WHERE id=?",
                          (cur.lastrowid,)).fetchone()
    finally:
        con.close()
    return jsonify(dict(row))


_LINK_FIELDS = {"platform", "label", "url", "icon", "display_order", "visible"}


@profile_bp.route("/api/ahb/profile-links/<int:lid>", methods=["PUT"])
def links_update(lid):
    d = request.get_json(silent=True) or {}
    fields = {k: v for k, v in d.items() if k in _LINK_FIELDS}
    if not fields:
        return jsonify({"error": "no updatable fields"}), 400
    if "url" in fields:
        try:
            fields["url"] = _normalize_url(fields["url"])
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
    if "visible" in fields:
        fields["visible"] = 1 if fields["visible"] else 0
    sets = ", ".join(f"{k}=?" for k in fields)
    con = _db()
    try:
        cur = con.execute(
            f"UPDATE ahb_profile_links SET {sets}, updated_at=CURRENT_TIMESTAMP "
            "WHERE id=?", (*fields.values(), lid))
        con.commit()
        if cur.rowcount == 0:
            return jsonify({"error": "not found"}), 404
    finally:
        con.close()
    return jsonify({"ok": True})


@profile_bp.route("/api/ahb/profile-links/<int:lid>", methods=["DELETE"])
def links_delete(lid):
    con = _db()
    try:
        cur = con.execute("DELETE FROM ahb_profile_links WHERE id=?", (lid,))
        con.commit()
        if cur.rowcount == 0:
            return jsonify({"error": "not found"}), 404
    finally:
        con.close()
    return jsonify({"ok": True})


@profile_bp.route("/api/ahb/profile-links/public", methods=["GET"])
def links_public():
    con = _db()
    try:
        rows = con.execute(
            "SELECT platform, label, url, icon FROM ahb_profile_links "
            "WHERE visible=1 ORDER BY display_order, id").fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        con.close()
    return jsonify({"items": [dict(r) for r in rows]})
