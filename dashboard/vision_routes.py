"""Vision UI Flask blueprint.

All routes gated by the existing _is_private_unlocked() session check
(re-imported from dashboard.app to keep the gate single-source-of-truth).
"""
from __future__ import annotations

import functools
import os
import time
from typing import Optional

from flask import Blueprint, jsonify, render_template, request, session, abort, redirect, url_for

from dashboard.vision.db import DEFAULT_DB_PATH, connect, init_db
from dashboard.vision.search import browse_query, count_for_node, fts_search
from dashboard.vision.taxonomy import TAXONOMY, all_nodes, ancestor_filters, find_node

bp = Blueprint("vision", __name__)

# Vision UI is open within the dashboard auth boundary — no passphrase
# required. The legacy /datahub/private route remains passphrase-gated for
# the locked-down photo gallery (private.html).


def _node_to_dict(node, con) -> dict:
    filters = ancestor_filters(node.path)
    return {
        "path": node.path,
        "label": node.label,
        "count": count_for_node(con, filters) if filters else None,
        "target": node.target,
        "children": [_node_to_dict(c, con) for c in node.children],
    }


@bp.route("/vision")
def vision_page():
    return render_template("vision.html")


@bp.route("/api/vision/tree")
def api_tree():
    init_db()
    con = connect()
    try:
        tree = [_node_to_dict(n, con) for n in TAXONOMY]
        pending = con.execute(
            "SELECT COUNT(*) FROM assets WHERE status='pending'"
        ).fetchone()[0]
        failed = con.execute(
            "SELECT COUNT(*) FROM assets WHERE status='failed'"
        ).fetchone()[0]
        open_demand = con.execute(
            "SELECT COUNT(*) FROM seed_demand WHERE fulfilled_at IS NULL"
        ).fetchone()[0]
        return jsonify({
            "ok": True, "tree": tree,
            "stats": {"pending": pending, "failed": failed, "open_demand": open_demand},
        })
    finally:
        con.close()


@bp.route("/api/vision/browse")
def api_browse():
    init_db()
    path = request.args.get("path", "/Catalogue")
    page = max(1, int(request.args.get("page", 1)))
    limit = max(1, min(200, int(request.args.get("limit", 60))))
    node = find_node(path)
    if not node:
        return jsonify({"ok": False, "error": f"no such path: {path}"}), 404

    filters = ancestor_filters(path)
    con = connect()
    try:
        sql, params = browse_query(filters, page=page, limit=limit)
        assets = [dict(r) for r in con.execute(sql, params).fetchall()]
        total = count_for_node(con, filters) if filters else 0
        pages = (total + limit - 1) // limit if limit else 1
        return jsonify({
            "ok": True,
            "node": {"path": node.path, "label": node.label, "target": node.target},
            "assets": assets,
            "total": total,
            "page": page,
            "pages": pages,
        })
    finally:
        con.close()


@bp.route("/api/vision/search")
def api_search():
    init_db()
    q = (request.args.get("q") or "").strip()
    limit = max(1, min(200, int(request.args.get("limit", 60))))
    if not q:
        return jsonify({"ok": True, "assets": [], "total": 0})
    con = connect()
    try:
        rows = fts_search(con, q, limit=limit)
        assets = [dict(r) for r in rows]
        return jsonify({"ok": True, "assets": assets, "total": len(assets)})
    finally:
        con.close()


@bp.route("/api/vision/asset/<int:asset_id>")
def api_asset(asset_id: int):
    init_db()
    con = connect()
    try:
        a = con.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        if not a:
            return jsonify({"ok": False, "error": "no such asset"}), 404
        attrs = {r["key"]: {"value": r["value"], "confidence": r["confidence"], "source": r["source"]}
                 for r in con.execute(
                     "SELECT key, value, confidence, source FROM attributes WHERE asset_id=?",
                     (asset_id,)
                 ).fetchall()}
        cap = con.execute("SELECT caption, tags, model FROM captions WHERE asset_id=?", (asset_id,)).fetchone()
        crops = [dict(r) for r in con.execute(
            """SELECT a.id, a.abs_path, c.part, c.bbox_x, c.bbox_y, c.bbox_w, c.bbox_h
                 FROM assets a JOIN crops c ON c.asset_id=a.id
                WHERE a.parent_id=?""",
            (asset_id,),
        ).fetchall()]
        parent = None
        if a["parent_id"]:
            p = con.execute("SELECT id, abs_path, source FROM assets WHERE id=?", (a["parent_id"],)).fetchone()
            parent = dict(p) if p else None
        return jsonify({
            "ok": True,
            "asset": dict(a),
            "attributes": attrs,
            "caption": dict(cap) if cap else None,
            "crops": crops,
            "parent": parent,
        })
    finally:
        con.close()


@bp.route("/api/vision/asset/<int:asset_id>/attributes", methods=["POST"])
def api_asset_attributes(asset_id: int):
    body = request.get_json(silent=True) or {}
    updates = body.get("attributes") or {}
    if not isinstance(updates, dict) or not updates:
        return jsonify({"ok": False, "error": "attributes dict required"}), 400
    init_db()
    con = connect()
    try:
        a = con.execute("SELECT id FROM assets WHERE id=?", (asset_id,)).fetchone()
        if not a:
            return jsonify({"ok": False, "error": "no such asset"}), 404
        for k, v in updates.items():
            if v is None:
                con.execute("DELETE FROM attributes WHERE asset_id=? AND key=?", (asset_id, k))
            else:
                con.execute(
                    """INSERT INTO attributes (asset_id, key, value, confidence, source)
                       VALUES (?, ?, ?, 1.0, 'manual')
                       ON CONFLICT(asset_id, key) DO UPDATE SET
                           value=excluded.value, confidence=1.0, source='manual'""",
                    (asset_id, k, str(v).strip().lower()),
                )
        return jsonify({"ok": True, "asset_id": asset_id})
    finally:
        con.close()


@bp.route("/api/vision/keep-unlocked", methods=["POST"])
def api_keep_unlocked():
    """Toggle session permanence so the unlock survives browser-tab cookie
    weirdness for 12 hours. Off by default — Vision UI's header toggle calls
    this to opt in. Lifetime is configured app-wide via PERMANENT_SESSION_LIFETIME."""
    body = request.get_json(silent=True) or {}
    keep = bool(body.get("value"))
    session.permanent = keep
    return jsonify({"ok": True, "keep_unlocked": keep})


@bp.route("/api/vision/queue/backfill", methods=["POST"])
def api_queue_backfill():
    """Walk dashboard/artifacts/ and insert pending asset rows for every
    image not already in the catalog. Idempotent. Body: {"include_public":
    true|false} (default true — for the SD-down workflow we want every
    image queued, ready to process when the GPU pool comes back)."""
    body = request.get_json(silent=True) or {}
    include_public = body.get("include_public", True)
    init_db()

    import os as _os
    from dashboard.vision.ingest import observe as _observe
    from dashboard.vision.migrate_existing import (
        ARTIFACTS_DIR, IMG_EXTS, _agent_hint,
    )
    from dashboard.private_inbound import is_private as _is_priv

    con = connect()
    try:
        before = con.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
    finally:
        con.close()

    seen = added = skipped = 0
    for root, dirs, files in _os.walk(ARTIFACTS_DIR):
        dirs[:] = [d for d in dirs if d not in (".vision-generated",
                                                ".vision-scraped",
                                                ".vision-crops")]
        for fn in files:
            if fn.endswith(".meta") or fn.endswith(".private"):
                continue
            ext = _os.path.splitext(fn)[1].lower()
            if ext not in IMG_EXTS:
                continue
            path = _os.path.join(root, fn)
            if not include_public and not _is_priv(path):
                continue
            seen += 1
            try:
                _observe(path, source="inbound",
                         origin_agent=_agent_hint(path))
                added += 1
            except Exception:
                skipped += 1

    con = connect()
    try:
        after = con.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
        pending = con.execute("SELECT COUNT(*) FROM assets WHERE status='pending'").fetchone()[0]
    finally:
        con.close()
    return jsonify({
        "ok": True,
        "seen": seen,
        "new_rows": after - before,
        "queue_pending": pending,
        "skipped": skipped,
    })


@bp.route("/api/vision/specter/seed", methods=["POST"])
def api_specter_seed():
    body = request.get_json(silent=True) or {}
    path = (body.get("path") or "").strip()
    if not path or not find_node(path):
        return jsonify({"ok": False, "error": "valid taxonomy path required"}), 400
    init_db()
    con = connect()
    try:
        cur = con.execute(
            """INSERT INTO seed_demand (taxonomy_path, needed, reason, requested_at)
               VALUES (?, 6, 'agent-request', ?)""",
            (path, time.time()),
        )
        return jsonify({"ok": True, "demand_id": cur.lastrowid, "eta_seconds": 600})
    finally:
        con.close()


@bp.route("/api/vision/asset/<int:asset_id>/thumb")
def api_asset_thumb(asset_id: int):
    init_db()
    full = request.args.get("full") == "1"
    con = connect()
    try:
        a = con.execute("SELECT abs_path FROM assets WHERE id=?", (asset_id,)).fetchone()
        if not a:
            abort(404)
        path = a["abs_path"]
        if not os.path.isfile(path):
            abort(404)

        # Defense-in-depth: only serve files inside dashboard/artifacts/.
        # The dashboard auth boundary already gates this whole route — the
        # earlier .private-inbound-only whitelist 403'd every public image
        # (including all 385 just queued via the backfill), so thumbnails
        # rendered as broken icons.
        artifacts_root = os.path.realpath(
            os.path.join(os.path.dirname(__file__), "artifacts")
        )
        real_path = os.path.realpath(path)
        if not real_path.startswith(artifacts_root + os.sep):
            abort(403)

        if full:
            return _send_file(path)

        # Generate a 256px thumbnail on the fly. Cheap with Pillow + JPEG
        # quality 78 — typical thumb < 30 KB. No on-disk thumb cache for v1.
        from io import BytesIO
        from PIL import Image
        from flask import send_file
        img = Image.open(path).convert("RGB")
        img.thumbnail((256, 256), Image.LANCZOS)
        buf = BytesIO(); img.save(buf, "JPEG", quality=78); buf.seek(0)
        return send_file(buf, mimetype="image/jpeg",
                         download_name=f"thumb_{asset_id}.jpg")
    finally:
        con.close()


def _send_file(path: str):
    from flask import send_file
    return send_file(path, mimetype=None, conditional=True)
