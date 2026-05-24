"""Flask blueprint for the live build-tree scaffold subsystem."""
import json
import os
import queue
from flask import Blueprint, jsonify, request, Response
from core.scaffold_engine import ScaffoldEngine, event_bus, NODE_TYPES

scaffold_bp = Blueprint("scaffold", __name__)


def _db_path():
    return os.environ.get(
        "BAZA_PROJECTS_DB",
        os.path.join(os.path.dirname(__file__), "baza_projects.db")
    )


def _engine():
    return ScaffoldEngine(_db_path())


def _project_exists(pid):
    import sqlite3
    con = sqlite3.connect(_db_path())
    try:
        row = con.execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone()
        return row is not None
    finally:
        con.close()


# ---------------- Scaffold graph ----------------

@scaffold_bp.route("/api/baza/projects/<pid>/scaffold/start", methods=["POST"])
def scaffold_start(pid):
    if not _project_exists(pid):
        return jsonify({"error": "project not found"}), 404
    body = request.get_json(silent=True) or {}
    description = (body.get("description") or "").strip()
    regenerate = bool(body.get("regenerate"))
    eng = _engine()
    if regenerate:
        for n in eng.get_nodes(pid):
            eng.delete_node(n["id"])
    root_id = eng.create_node(
        pid, node_type="root",
        title=description[:80] or "New build",
        description=description,
        status="in_progress",
        payload={"description": description}
    )
    # Dispatch (best-effort)
    task_id = None
    try:
        from core.intent_dispatcher import dispatch
        env = {"intent": "scaffold_decompose",
               "project_id": pid,
               "root_node_id": root_id,
               "description": description,
               "actor": "user"}
        res = dispatch(env, extra={})
        if isinstance(res, dict):
            task_id = res.get("task_id")
        elif isinstance(res, tuple) and isinstance(res[0], dict):
            task_id = res[0].get("task_id")
    except Exception as e:
        eng.emit_event(pid, node_id=root_id, event_type="note",
                       actor="system",
                       payload={"warning": f"dispatch unavailable: {e}"})
    return jsonify({"root_node_id": root_id, "task_id": task_id}), 202


@scaffold_bp.route("/api/baza/projects/<pid>/scaffold", methods=["GET"])
def scaffold_get(pid):
    eng = _engine()
    nodes = eng.get_nodes(pid)
    for n in nodes:
        if n.get("payload_json"):
            try:
                n["payload"] = json.loads(n["payload_json"])
            except Exception:
                n["payload"] = {}
        else:
            n["payload"] = {}
    return jsonify({
        "nodes": nodes,
        "edges": eng.get_edges(pid),
        "progress_pct": eng.progress_pct(pid),
        "has_star": eng.has_star(pid),
    })


@scaffold_bp.route("/api/baza/projects/<pid>/scaffold/node", methods=["POST"])
def scaffold_node_create(pid):
    body = request.get_json(silent=True) or {}
    nt = body.get("node_type")
    title = (body.get("title") or "").strip()
    if not nt or nt not in NODE_TYPES:
        return jsonify({"error": "invalid node_type"}), 400
    if not title:
        return jsonify({"error": "title required"}), 400
    eng = _engine()
    nid = eng.create_node(
        pid,
        node_type=nt,
        title=title,
        description=body.get("description", ""),
        parent_id=body.get("parent_id"),
        weight=body.get("weight"),
        agent=body.get("agent"),
        payload=body.get("payload"),
    )
    return jsonify({"id": nid}), 201


@scaffold_bp.route("/api/baza/projects/<pid>/scaffold/node/<int:nid>", methods=["PATCH"])
def scaffold_node_patch(pid, nid):
    eng = _engine()
    n = eng.get_node(nid)
    if not n or n["project_id"] != pid:
        return jsonify({"error": "not found"}), 404
    body = request.get_json(silent=True) or {}
    fields = {}
    for k in ("title", "description", "status", "weight", "agent_assigned"):
        if k in body:
            fields[k] = body[k]
    if "payload" in body:
        fields["payload_json"] = json.dumps(body["payload"], default=str)
    eng.update_node(nid, **fields)
    return jsonify({"ok": True})


@scaffold_bp.route("/api/baza/projects/<pid>/scaffold/node/<int:nid>", methods=["DELETE"])
def scaffold_node_delete(pid, nid):
    eng = _engine()
    n = eng.get_node(nid)
    if not n or n["project_id"] != pid:
        return jsonify({"error": "not found"}), 404
    eng.delete_node(nid)
    return jsonify({"ok": True})


@scaffold_bp.route("/api/baza/projects/<pid>/scaffold/node/<int:nid>/run", methods=["POST"])
def scaffold_node_run(pid, nid):
    eng = _engine()
    n = eng.get_node(nid)
    if not n or n["project_id"] != pid:
        return jsonify({"error": "not found"}), 404
    eng.update_node(nid, status="pending", started_at=None, completed_at=None)
    eng.emit_event(pid, node_id=nid, event_type="rerun_requested", actor="user")
    return jsonify({"ok": True})


@scaffold_bp.route("/api/baza/projects/<pid>/scaffold/node/<int:nid>/override", methods=["POST"])
def scaffold_node_override(pid, nid):
    eng = _engine()
    n = eng.get_node(nid)
    if not n or n["project_id"] != pid:
        return jsonify({"error": "not found"}), 404
    body = request.get_json(silent=True) or {}
    chosen = (body.get("chosen_option") or "").strip()
    if not chosen:
        return jsonify({"error": "chosen_option required"}), 400
    eng.override_decision(nid, chosen_option=chosen, reason=body.get("reason", ""))
    return jsonify({"ok": True})


@scaffold_bp.route("/api/baza/projects/<pid>/scaffold/node/<int:nid>/note", methods=["POST"])
def scaffold_node_note(pid, nid):
    eng = _engine()
    n = eng.get_node(nid)
    if not n or n["project_id"] != pid:
        return jsonify({"error": "not found"}), 404
    body = request.get_json(silent=True) or {}
    note = (body.get("note") or "").strip()
    if not note:
        return jsonify({"error": "note required"}), 400
    eng.emit_event(pid, node_id=nid, event_type="note", actor="user",
                   payload={"note": note})
    return jsonify({"ok": True})


@scaffold_bp.route("/api/baza/projects/<pid>/scaffold/pause", methods=["POST"])
def scaffold_pause(pid):
    import sqlite3
    con = sqlite3.connect(_db_path())
    try:
        con.execute("UPDATE projects SET scaffold_paused=1 WHERE id=?", (pid,))
        con.commit()
    finally:
        con.close()
    _engine().emit_event(pid, event_type="project_paused", actor="user")
    return jsonify({"ok": True, "paused": True})


@scaffold_bp.route("/api/baza/projects/<pid>/scaffold/resume", methods=["POST"])
def scaffold_resume(pid):
    import sqlite3
    con = sqlite3.connect(_db_path())
    try:
        con.execute("UPDATE projects SET scaffold_paused=0 WHERE id=?", (pid,))
        con.commit()
    finally:
        con.close()
    _engine().emit_event(pid, event_type="project_resumed", actor="user")
    return jsonify({"ok": True, "paused": False})


# ---------------- SSE ----------------

@scaffold_bp.route("/api/baza/projects/<pid>/scaffold/stream", methods=["GET"])
def scaffold_stream(pid):
    def gen():
        q = queue.Queue()
        def handler(evt):
            try:
                q.put_nowait(evt)
            except Exception:
                pass
        event_bus.subscribe(pid, handler)
        try:
            # Initial hello so the client knows it connected
            yield f"event: hello\ndata: {json.dumps({'project_id': pid})}\n\n"
            while True:
                try:
                    evt = q.get(timeout=15)
                    yield f"event: {evt['event_type']}\ndata: {json.dumps(evt, default=str)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            event_bus.unsubscribe(pid, handler)
    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})
