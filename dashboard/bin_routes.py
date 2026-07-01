# dashboard/bin_routes.py
"""Flask Blueprint for the Baza Bin (/api/bin/*). Thin HTTP layer over bin_store."""
import os
import mimetypes
from flask import Blueprint, request, jsonify, send_from_directory

try:
    from dashboard import bin_store
except ImportError:
    import bin_store

bin_bp = Blueprint("baza_bin", __name__)


@bin_bp.route("/api/bin/list")
def bin_list():
    q = (request.args.get("q") or "").strip() or None
    kind = (request.args.get("kind") or "").strip() or None
    try:
        limit = int(request.args.get("limit", 100))
        offset = int(request.args.get("offset", 0))
    except (TypeError, ValueError):
        limit, offset = 100, 0
    items = [bin_store.to_public(i)
             for i in bin_store.list_items(q=q, kind=kind, limit=limit, offset=offset)]
    return jsonify({"ok": True, "count": len(items), "items": items})


@bin_bp.route("/api/bin/serve/<token>")
def bin_serve(token):
    fpath = bin_store.resolve_token(token)
    if not fpath:
        return jsonify({"error": "Not found"}), 404
    return send_from_directory(os.path.dirname(fpath), os.path.basename(fpath))


@bin_bp.route("/api/bin/upload", methods=["POST"])
def bin_upload():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "no file"}), 400
    mime = f.mimetype or mimetypes.guess_type(f.filename)[0] or "application/octet-stream"
    data = f.read()
    item = bin_store.add_file(filename=f.filename, data=data, mime_type=mime,
                              source="upload")
    return jsonify({"ok": True, "item": bin_store.to_public(item)})


@bin_bp.route("/api/bin/delete", methods=["POST"])
def bin_delete():
    body = request.get_json(silent=True) or {}
    item_id = (body.get("id") or "").strip()
    if not bin_store.delete(item_id):
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True})
