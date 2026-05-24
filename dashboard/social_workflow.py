"""Social Studio v2.2 — templates, tags, bulk ops, versions, approval log."""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))


def _db():
    path = os.environ.get("BAZA_DASHBOARD_DB",
                          os.path.join(_HERE, "baza_projects.db"))
    con = sqlite3.connect(path, timeout=8.0)
    con.row_factory = sqlite3.Row
    return con


TEMPLATE_WRITABLE = {
    "name", "caption_template", "hashtag_set", "platform_targets",
    "first_comment_template", "music_id", "voiceover_script",
}


def _row_to_template(r):
    d = dict(r)
    try:
        d["platform_targets"] = json.loads(d["platform_targets"]) if d.get("platform_targets") else []
    except Exception:
        d["platform_targets"] = []
    return d


def _interpolate(template: str, variables: dict) -> str:
    if not template:
        return ""
    def repl(m):
        key = m.group(1).strip()
        return str(variables.get(key, m.group(0)))
    return re.sub(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}", repl, template)


def register(bp):
    from flask import jsonify, request

    @bp.route("/api/ahb/social/templates", methods=["GET"])
    def social_templates_list():
        con = _db()
        try:
            rows = con.execute("SELECT * FROM ahb_social_post_templates ORDER BY id DESC").fetchall()
        finally:
            con.close()
        return jsonify({"items": [_row_to_template(r) for r in rows]})

    @bp.route("/api/ahb/social/templates", methods=["POST"])
    def social_templates_create():
        data = request.get_json(silent=True) or {}
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name required"}), 400
        cols, vals = ["name"], [name]
        for k, v in data.items():
            if k == "name" or k not in TEMPLATE_WRITABLE:
                continue
            cols.append(k)
            vals.append(json.dumps(v) if isinstance(v, (list, dict)) else v)
        con = _db()
        try:
            cur = con.execute(
                f"INSERT INTO ahb_social_post_templates ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
                vals,
            )
            con.commit()
            tid = cur.lastrowid
        finally:
            con.close()
        return jsonify({"id": tid})

    @bp.route("/api/ahb/social/templates/<int:tid>", methods=["PUT"])
    def social_templates_update(tid: int):
        data = request.get_json(silent=True) or {}
        sets, vals = [], []
        for k, v in data.items():
            if k not in TEMPLATE_WRITABLE:
                continue
            sets.append(f"{k}=?")
            vals.append(json.dumps(v) if isinstance(v, (list, dict)) else v)
        if not sets:
            return jsonify({"error": "no writable fields"}), 400
        sets.append("updated_at=?"); vals.append(datetime.utcnow().isoformat(timespec="seconds"))
        vals.append(tid)
        con = _db()
        try:
            con.execute(f"UPDATE ahb_social_post_templates SET {','.join(sets)} WHERE id=?", vals)
            con.commit()
        finally:
            con.close()
        return jsonify({"ok": True})

    @bp.route("/api/ahb/social/templates/<int:tid>", methods=["DELETE"])
    def social_templates_delete(tid: int):
        con = _db()
        try:
            con.execute("DELETE FROM ahb_social_post_templates WHERE id=?", (tid,))
            con.commit()
        finally:
            con.close()
        return jsonify({"ok": True})

    @bp.route("/api/ahb/social/templates/<int:tid>/apply", methods=["POST"])
    def social_templates_apply(tid: int):
        data = request.get_json(silent=True) or {}
        variables = data.get("variables") or {}
        con = _db()
        try:
            r = con.execute("SELECT * FROM ahb_social_post_templates WHERE id=?", (tid,)).fetchone()
        finally:
            con.close()
        if not r:
            return jsonify({"error": "not found"}), 404
        t = _row_to_template(r)
        from datetime import date
        variables.setdefault("date", date.today().isoformat())
        return jsonify({
            "caption": _interpolate(t["caption_template"] or "", variables),
            "hashtags": t["hashtag_set"] or "",
            "first_comment": _interpolate(t.get("first_comment_template") or "", variables),
            "platform_targets": t["platform_targets"],
            "music_id": t.get("music_id"),
            "voiceover_script": t.get("voiceover_script"),
            "template_id": tid,
        })
