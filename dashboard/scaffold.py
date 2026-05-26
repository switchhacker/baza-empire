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


# ---------------- BOM ----------------
import sqlite3

BOM_WRITABLE = {"name", "part_number", "vendor", "url", "qty", "unit_price",
                "status", "notes", "node_id"}


@scaffold_bp.route("/api/baza/projects/<pid>/bom", methods=["GET"])
def bom_list(pid):
    con = sqlite3.connect(_db_path())
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT * FROM project_bom WHERE project_id=? ORDER BY in_hand ASC, id DESC",
            (pid,)
        ).fetchall()
    finally:
        con.close()
    return jsonify({"items": [dict(r) for r in rows]})


def _build_schematic_from_bom(eng, pid, schematic_node_id, preserve_existing=True):
    """Build/refresh the schematic payload for `schematic_node_id` from
    the project's current BOM. If preserve_existing=True, keep the
    positions and labels of components that already exist in the schematic
    (matched by component_id), and only APPEND newly-matched BOM rows
    that aren't yet represented. Returns dict with counts.
    """
    import json as _json
    import sqlite3 as _sq
    from core.baza_components_library import match_component, get_component

    # Fetch BOM
    cur = _sq.connect(_db_path()); cur.row_factory = _sq.Row
    try:
        bom_rows = cur.execute(
            "SELECT * FROM project_bom WHERE project_id=?", (pid,)
        ).fetchall()
    finally:
        cur.close()

    # Load existing schematic (so we preserve user positions/labels)
    node = eng.get_node(schematic_node_id)
    existing_payload = _json.loads(node.get("payload_json") or "{}")
    existing_schem = existing_payload.get("schematic") or {"components": [], "wires": [], "notes": ""}
    existing_components = existing_schem.get("components", [])
    existing_wires = existing_schem.get("wires", [])

    # Index existing by component_id (first occurrence kept)
    seen_component_ids = {c.get("component_id") for c in existing_components}
    components_out = list(existing_components) if preserve_existing else []
    newly_added_instance_ids = []

    # Append newly-matched BOM rows that aren't already represented
    added_count = 0
    for b in bom_rows:
        matched = match_component(b["name"] or "")
        if not matched:
            continue
        if matched["id"] in seen_component_ids and preserve_existing:
            continue  # already in schematic
        # Position new component to the right of existing ones
        i = len(components_out)
        new_inst_id = f"c{i+1}"
        components_out.append({
            "instance_id": new_inst_id,
            "component_id": matched["id"],
            "x": 60 + (i % 4) * 250,
            "y": 60 + (i // 4) * 250,
            "label": (b["name"] or matched["name"])[:40],
        })
        seen_component_ids.add(matched["id"])
        newly_added_instance_ids.append(new_inst_id)
        added_count += 1
        if len(components_out) >= 16:
            break

    # Build/extend wire layout from MCU to each non-MCU component.
    # On a fresh schematic (no existing wires), wire ALL components.
    # On append (existing wires present), wire only the NEW components.
    wires_out = list(existing_wires) if preserve_existing else []
    if components_out:
        mcu = next((c for c in components_out
                    if (get_component(c["component_id"]) or {}).get("category") == "mcu"), None)
        if mcu:
            mcu_def = get_component(mcu["component_id"]) or {"pins": []}
            mcu_power = next((p["name"] for p in mcu_def["pins"] if p["kind"] == "power"), None)
            mcu_gnd = next((p["name"] for p in mcu_def["pins"] if p["kind"] == "ground"), None)
            gpio_pool = [p["name"] for p in mcu_def["pins"] if p["kind"] == "gpio"]
            # Count gpio slots already consumed by existing wires
            gpio_idx = sum(1 for w in existing_wires if w.get("color") == "signal")
            # Decide which components need wiring this pass
            if existing_wires:
                wire_targets = [c for c in components_out
                                if c["instance_id"] in newly_added_instance_ids
                                and c["instance_id"] != mcu["instance_id"]]
            else:
                wire_targets = [c for c in components_out
                                if c["instance_id"] != mcu["instance_id"]]
            for c in wire_targets:
                comp_def = get_component(c["component_id"]) or {"pins": []}
                for p in comp_def["pins"]:
                    if p["kind"] == "ground" and mcu_gnd:
                        wires_out.append({"from": f"{mcu['instance_id']}.{mcu_gnd}",
                                          "to": f"{c['instance_id']}.{p['name']}",
                                          "color": "ground"})
                    elif p["kind"] == "power" and mcu_power:
                        wires_out.append({"from": f"{mcu['instance_id']}.{mcu_power}",
                                          "to": f"{c['instance_id']}.{p['name']}",
                                          "color": "power"})
                    elif p["kind"] in ("signal", "gpio", "pwm") and gpio_idx < len(gpio_pool):
                        wires_out.append({"from": f"{mcu['instance_id']}.{gpio_pool[gpio_idx]}",
                                          "to": f"{c['instance_id']}.{p['name']}",
                                          "color": "signal"})
                        gpio_idx += 1
                        break

    schematic = {
        "components": components_out,
        "wires": wires_out,
        "notes": existing_schem.get("notes") or "Auto-proposed from BOM. Drag to rearrange; click pins to wire."
    }
    existing_payload["schematic"] = schematic
    eng.update_node(schematic_node_id, payload_json=_json.dumps(existing_payload, default=str))
    return {"total_components": len(components_out), "added": added_count}


@scaffold_bp.route("/api/baza/projects/<pid>/bom", methods=["POST"])
def bom_create(pid):
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    con = sqlite3.connect(_db_path())
    try:
        cur = con.execute("""
            INSERT INTO project_bom
              (project_id, node_id, name, part_number, vendor, url, qty,
               unit_price, status, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (pid, body.get("node_id"), name, body.get("part_number"),
              body.get("vendor"), body.get("url"), int(body.get("qty") or 1),
              body.get("unit_price"), body.get("status", "researched"),
              body.get("notes")))
        bid = cur.lastrowid
        con.commit()
    finally:
        con.close()

    # Schematic sync: create on first hw BOM, append on subsequent hw BOMs
    try:
        eng = _engine()
        import sqlite3 as _sq
        from core.baza_components_library import match_component
        is_hw = match_component(name) is not None
        if is_hw:
            _con = _sq.connect(_db_path()); _con.row_factory = _sq.Row
            try:
                existing_schem_row = _con.execute(
                    "SELECT id FROM project_scaffold_nodes "
                    "WHERE project_id=? AND node_type='schematic' "
                    "ORDER BY id ASC LIMIT 1",
                    (pid,)
                ).fetchone()
            finally:
                _con.close()

            if existing_schem_row is None:
                # First hw BOM — create schematic node, then build payload
                rn = eng.get_nodes(pid)
                root = next((n for n in rn if n["node_type"] == "root"), None)
                schem_id = eng.create_node(
                    pid,
                    node_type="schematic",
                    title="Wiring schematic",
                    description="Auto-generated from your BOM. Drag components to rearrange; click pins to wire.",
                    parent_id=(root["id"] if root else None),
                    status="in_progress",
                    payload={"description": f"Initial wiring for project {pid}"}
                )
                try:
                    result = _build_schematic_from_bom(eng, pid, schem_id, preserve_existing=True)
                    eng.emit_event(pid, node_id=schem_id, event_type="schematic_proposed",
                                   actor="system",
                                   payload={"component_count": result["total_components"]})
                except Exception as e:
                    eng.emit_event(pid, node_id=schem_id, event_type="note", actor="system",
                                   payload={"warning": f"auto-propose failed: {e}"})
            else:
                # Subsequent hw BOM — append to existing schematic
                schem_id = existing_schem_row["id"]
                try:
                    result = _build_schematic_from_bom(eng, pid, schem_id, preserve_existing=True)
                    if result["added"] > 0:
                        eng.emit_event(pid, node_id=schem_id, event_type="schematic_updated",
                                       actor="system",
                                       payload={"added": result["added"],
                                                "total_components": result["total_components"]})
                except Exception as e:
                    eng.emit_event(pid, node_id=schem_id, event_type="note", actor="system",
                                   payload={"warning": f"auto-append failed: {e}"})
    except Exception as e:
        # Never crash the BOM POST because of schematic logic
        print(f"[schematic sync] {e}", flush=True)

    _engine().emit_event(pid, node_id=body.get("node_id"), event_type="bom_added",
                         actor="user", payload={"bom_id": bid, "name": name})
    return jsonify({"id": bid}), 201


@scaffold_bp.route("/api/baza/projects/<pid>/bom/<int:bid>", methods=["PATCH"])
def bom_patch(pid, bid):
    body = request.get_json(silent=True) or {}
    sets, vals = [], []
    for k, v in body.items():
        if k in BOM_WRITABLE:
            sets.append(f"{k}=?"); vals.append(v)
    if not sets:
        return jsonify({"error": "nothing to update"}), 400
    sets.append("updated_at=CURRENT_TIMESTAMP"); vals.extend([bid, pid])
    con = sqlite3.connect(_db_path())
    try:
        cur = con.execute(
            f"UPDATE project_bom SET {', '.join(sets)} WHERE id=? AND project_id=?",
            vals
        )
        if cur.rowcount == 0:
            return jsonify({"error": "not found"}), 404
        con.commit()
    finally:
        con.close()
    return jsonify({"ok": True})


@scaffold_bp.route("/api/baza/projects/<pid>/bom/<int:bid>", methods=["DELETE"])
def bom_delete(pid, bid):
    con = sqlite3.connect(_db_path())
    try:
        cur = con.execute("DELETE FROM project_bom WHERE id=? AND project_id=?",
                          (bid, pid))
        if cur.rowcount == 0:
            return jsonify({"error": "not found"}), 404
        con.commit()
    finally:
        con.close()
    return jsonify({"ok": True})


@scaffold_bp.route("/api/baza/projects/<pid>/bom/<int:bid>/toggle-hand", methods=["POST"])
def bom_toggle_hand(pid, bid):
    eng = _engine()
    con = sqlite3.connect(_db_path())
    con.row_factory = sqlite3.Row
    try:
        row = con.execute("SELECT * FROM project_bom WHERE id=? AND project_id=?",
                          (bid, pid)).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        new_val = 0 if row["in_hand"] else 1
        if new_val:
            con.execute(
                "UPDATE project_bom SET in_hand=?, in_hand_at=CURRENT_TIMESTAMP, "
                "status=CASE WHEN status NOT IN ('cancelled') THEN 'received' ELSE status END "
                "WHERE id=?",
                (new_val, bid)
            )
        else:
            con.execute(
                "UPDATE project_bom SET in_hand=?, in_hand_at=NULL WHERE id=?",
                (new_val, bid)
            )
        con.commit()
        node_id = row["node_id"]
        if new_val and node_id:
            node = eng.get_node(node_id)
            if node and node["status"] == "awaiting_part":
                eng.update_node(node_id, status="pending")
    finally:
        con.close()
    eng.emit_event(pid, node_id=row["node_id"], event_type="bom_in_hand",
                   actor="user",
                   payload={"bom_id": bid, "in_hand": bool(new_val)})
    return jsonify({"ok": True, "in_hand": bool(new_val)})


@scaffold_bp.route("/api/baza/projects/<pid>/bom/<int:bid>/promote-inventory", methods=["POST"])
def bom_promote_inventory(pid, bid):
    eng = _engine()
    con = sqlite3.connect(_db_path())
    con.row_factory = sqlite3.Row
    try:
        b = con.execute("SELECT * FROM project_bom WHERE id=? AND project_id=?",
                        (bid, pid)).fetchone()
        if not b:
            return jsonify({"error": "not found"}), 404
        cur = con.execute("""
            INSERT INTO baza_inventory
              (category, name, part_number, quantity, unit_price, vendor, url, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, ("auto-promoted", b["name"], b["part_number"], b["qty"],
              b["unit_price"], b["vendor"], b["url"], b["notes"]))
        inv_id = cur.lastrowid
        con.execute("UPDATE project_bom SET inventory_id=? WHERE id=?", (inv_id, bid))
        con.commit()
    finally:
        con.close()
    eng.emit_event(pid, node_id=b["node_id"], event_type="promoted_to_inventory",
                   actor="user", payload={"bom_id": bid, "inventory_id": inv_id})
    return jsonify({"ok": True, "inventory_id": inv_id})


# ---------------- Global Inventory + Equipment ----------------

INV_WRITABLE = {"category", "name", "part_number", "quantity", "location",
                "condition", "unit_price", "vendor", "url", "notes"}
EQUIP_WRITABLE = {"name", "type", "location", "status", "in_use_by", "notes"}


def _crud_helpers(table, writable_set):
    """Returns (list_fn, create_fn, patch_fn, delete_fn) for a global table."""
    def _list():
        con = sqlite3.connect(_db_path()); con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                f"SELECT * FROM {table} ORDER BY id DESC"
            ).fetchall()
        finally:
            con.close()
        return jsonify({"items": [dict(r) for r in rows]})

    def _create():
        body = request.get_json(silent=True) or {}
        name = (body.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name required"}), 400
        cols = [k for k in body if k in writable_set]
        cols_sql = ", ".join(cols)
        ph = ", ".join("?" for _ in cols)
        vals = [body[k] for k in cols]
        con = sqlite3.connect(_db_path())
        try:
            cur = con.execute(
                f"INSERT INTO {table} ({cols_sql}) VALUES ({ph})", vals
            )
            new_id = cur.lastrowid
            con.commit()
        finally:
            con.close()
        return jsonify({"id": new_id}), 201

    def _patch(item_id):
        body = request.get_json(silent=True) or {}
        sets, vals = [], []
        for k, v in body.items():
            if k in writable_set:
                sets.append(f"{k}=?"); vals.append(v)
        if not sets:
            return jsonify({"error": "nothing to update"}), 400
        sets.append("updated_at=CURRENT_TIMESTAMP"); vals.append(item_id)
        con = sqlite3.connect(_db_path())
        try:
            cur = con.execute(
                f"UPDATE {table} SET {', '.join(sets)} WHERE id=?", vals
            )
            if cur.rowcount == 0:
                return jsonify({"error": "not found"}), 404
            con.commit()
        finally:
            con.close()
        return jsonify({"ok": True})

    def _delete(item_id):
        con = sqlite3.connect(_db_path())
        try:
            cur = con.execute(f"DELETE FROM {table} WHERE id=?", (item_id,))
            if cur.rowcount == 0:
                return jsonify({"error": "not found"}), 404
            con.commit()
        finally:
            con.close()
        return jsonify({"ok": True})

    return _list, _create, _patch, _delete


_inv_list, _inv_create, _inv_patch, _inv_delete = _crud_helpers("baza_inventory", INV_WRITABLE)
_eq_list, _eq_create, _eq_patch, _eq_delete = _crud_helpers("baza_equipment", EQUIP_WRITABLE)

scaffold_bp.add_url_rule("/api/baza/inventory", "inv_list", _inv_list, methods=["GET"])
scaffold_bp.add_url_rule("/api/baza/inventory", "inv_create", _inv_create, methods=["POST"])
scaffold_bp.add_url_rule("/api/baza/inventory/<int:item_id>", "inv_patch", _inv_patch, methods=["PATCH"])
scaffold_bp.add_url_rule("/api/baza/inventory/<int:item_id>", "inv_delete", _inv_delete, methods=["DELETE"])

scaffold_bp.add_url_rule("/api/baza/equipment", "eq_list", _eq_list, methods=["GET"])
scaffold_bp.add_url_rule("/api/baza/equipment", "eq_create", _eq_create, methods=["POST"])
scaffold_bp.add_url_rule("/api/baza/equipment/<int:item_id>", "eq_patch", _eq_patch, methods=["PATCH"])
scaffold_bp.add_url_rule("/api/baza/equipment/<int:item_id>", "eq_delete", _eq_delete, methods=["DELETE"])


# ---------------- Components catalog ----------------

@scaffold_bp.route("/api/baza/components", methods=["GET"])
def components_catalog():
    from core.baza_components_library import list_components
    return jsonify({"items": list_components()})


# ---------------- Supplies needed (Phase 3 stub returning real data) ----------------

@scaffold_bp.route("/api/baza/supplies/needed", methods=["GET"])
def supplies_needed():
    con = sqlite3.connect(_db_path()); con.row_factory = sqlite3.Row
    try:
        rows = con.execute("""
            SELECT name, part_number, vendor, url, SUM(qty) as total_qty,
                   MIN(unit_price) as best_price, COUNT(*) as project_count
            FROM project_bom
            WHERE in_hand = 0 AND status NOT IN ('cancelled', 'received')
            GROUP BY name, part_number
            ORDER BY total_qty DESC
        """).fetchall()
    finally:
        con.close()
    return jsonify({"items": [dict(r) for r in rows]})


# ============================================================================
#  PCB Vision — board photo → labeled overlays + clean schematic
#  (Spec: docs/superpowers/specs/2026-05-26-pcb-vision-design.md)
# ============================================================================

import hashlib
import mimetypes
import subprocess
import threading
from pathlib import Path
from flask import send_file, send_from_directory, abort, session

REPO_ROOT     = Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = REPO_ROOT / "dashboard" / "artifacts"
SKILL_PATH    = REPO_ROOT / "skills" / "shared" / "scaffold_analyze_pcb_image.py"
VENV_PY       = REPO_ROOT / "venv" / "bin" / "python"
PCB_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".bmp", ".tiff"}

# Browseable image roots — each is shown as a chip in the PCB picker.
# `requires_unlock` chips are only listed/walkable when the private vault is open.
# `project_scoped` chips substitute <pid> at request time.
# Cloud root is split into honest sub-locations so the "Data Hub" chip means
# what the user uploaded TO Data Hub, not the entire archive (Photos library,
# drive imports, etc. get their own chips).
_CLOUD_ROOT = os.environ.get("BAZA_CLOUD_ROOT", "/mnt/empirepool/cloud/1")

BROWSE_ROOTS = [
    {"key":"datahub_uploads","label":"📤 Data Hub uploads",
     "path":f"{_CLOUD_ROOT}/Uploads", "requires_unlock":False, "max_depth":None},
    {"key":"datahub_photos", "label":"🖼 Photos library",
     "path":f"{_CLOUD_ROOT}/Photos",  "requires_unlock":False, "max_depth":None,
     "follow_symlinks":True},
    {"key":"datahub_receipts","label":"🧾 Receipts upload",
     "path":f"{_CLOUD_ROOT}/Receipts upload", "requires_unlock":False, "max_depth":None},
    {"key":"datahub_imports","label":"💿 Drive imports",
     "path":f"{_CLOUD_ROOT}/Imports", "requires_unlock":False, "max_depth":None},
    {"key":"desktop",  "label":"🖥 Desktop",     "path":str(Path.home() / "Desktop"),
     "requires_unlock":False, "max_depth":3},
    {"key":"documents","label":"📄 Documents",  "path":str(Path.home() / "Documents"),
     "requires_unlock":False, "max_depth":4},
    {"key":"downloads","label":"⬇ Downloads",   "path":str(Path.home() / "Downloads"),
     "requires_unlock":False, "max_depth":3},
    {"key":"pictures", "label":"🖼 Pictures",    "path":str(Path.home() / "Pictures"),
     "requires_unlock":False, "max_depth":5},
    {"key":"nextcloud","label":"☁ Nextcloud",   "path":str(Path.home() / "nextcloud"),
     "requires_unlock":False, "max_depth":5},
    {"key":"project",  "label":"🗂 This project",
     "path":"__PROJECT__", "requires_unlock":False, "max_depth":None, "project_scoped":True},
    {"key":"vault",    "label":"🔒 Private vault",
     "path":str(REPO_ROOT / "dashboard" / "artifacts" / ".private-inbound"),
     "requires_unlock":True, "max_depth":None},
    # legacy/back-compat alias — points at the cloud root for any caller still
    # passing root=datahub. Lists nothing useful (subdirs covered by chips above).
    {"key":"datahub",  "label":"📁 Cloud root (legacy)",
     "path":_CLOUD_ROOT, "requires_unlock":False, "max_depth":1, "hidden":True},
]


def _is_private_unlocked() -> bool:
    """Mirrors dashboard.app._is_private_unlocked without importing it (avoid cycle)."""
    try:
        return bool(session.get("private_unlocked"))
    except Exception:
        return False


def _pcb_dir(project_id: str) -> Path:
    d = ARTIFACTS_DIR / project_id / "pcb"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _spawn_analyze(node_id: int, mode: str = "merge") -> None:
    """Fire the vision skill subprocess and forget — UI polls/streams results."""
    args = json.dumps({"node_id": int(node_id), "mode": mode})
    env  = {**os.environ, "SKILL_ARGS": args, "PYTHONUNBUFFERED": "1"}

    def _run():
        try:
            subprocess.run(
                [str(VENV_PY), str(SKILL_PATH)],
                env=env, cwd=str(REPO_ROOT),
                capture_output=True, text=True, timeout=180,
            )
        except subprocess.TimeoutExpired:
            pass
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()


def _create_pcb_vision_node(eng, project_id: str, parent_id, title: str, payload: dict):
    return eng.create_node(
        project_id=project_id,
        node_type="pcb_vision",
        title=title,
        description="Board / circuit photo analysis",
        parent_id=parent_id,
        payload=payload,
        status="running",
    )


# ---------------- Upload (multipart) ------------------------------------------

@scaffold_bp.route("/api/baza/projects/<pid>/scaffold/pcb_vision/upload",
                   methods=["POST"])
def pcb_vision_upload(pid):
    if not _project_exists(pid):
        return jsonify({"ok": False, "error": "project_not_found"}), 404
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "file required"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"ok": False, "error": "filename required"}), 400
    ext = Path(f.filename).suffix.lower()
    if ext not in PCB_IMAGE_EXTS:
        return jsonify({"ok": False, "error": f"unsupported extension {ext}"}), 415

    raw = f.read()
    if not raw:
        return jsonify({"ok": False, "error": "empty file"}), 400
    sha = hashlib.sha256(raw).hexdigest()[:8]
    dest = _pcb_dir(pid) / f"{sha}{ext}"
    if not dest.exists():
        dest.write_bytes(raw)

    parent_id = (request.form.get("parent_id") or "").strip() or None
    if parent_id:
        try: parent_id = int(parent_id)
        except ValueError: parent_id = None

    title = (request.form.get("title") or f.filename).strip()[:200]
    payload = {
        "image_path": str(dest),
        "image_source": "upload",
        "overlays": [],
        "schematic": {"components": [], "wires": [], "notes": ""},
        "best_guess_wires": False,
    }
    eng = _engine()
    nid = _create_pcb_vision_node(eng, pid, parent_id, title, payload)
    _spawn_analyze(nid, mode="reset")
    return jsonify({"ok": True, "node_id": nid})


# ---------------- Create from Data Hub ----------------------------------------

@scaffold_bp.route("/api/baza/projects/<pid>/scaffold/pcb_vision/create_from_datahub",
                   methods=["POST"])
def pcb_vision_create_from_datahub(pid):
    if not _project_exists(pid):
        return jsonify({"ok": False, "error": "project_not_found"}), 404
    body = request.get_json(silent=True) or {}
    datahub_path = (body.get("datahub_path") or "").strip()
    is_private = bool(body.get("is_private"))
    if not datahub_path:
        return jsonify({"ok": False, "error": "datahub_path required"}), 400
    p = Path(datahub_path)
    if not p.is_absolute() or not p.exists() or not p.is_file():
        return jsonify({"ok": False, "error": "file not found"}), 404
    if is_private and not _is_private_unlocked():
        return jsonify({"ok": False, "error": "vault locked"}), 403
    if p.suffix.lower() not in PCB_IMAGE_EXTS:
        return jsonify({"ok": False, "error": "not an image"}), 415

    parent_id = body.get("parent_id")
    if parent_id is not None:
        try: parent_id = int(parent_id)
        except (TypeError, ValueError): parent_id = None

    title = (body.get("title") or p.name)[:200]
    payload = {
        "image_path": str(p),
        "image_source": "datahub_private" if is_private else "datahub",
        "overlays": [],
        "schematic": {"components": [], "wires": [], "notes": ""},
        "best_guess_wires": False,
    }
    eng = _engine()
    nid = _create_pcb_vision_node(eng, pid, parent_id, title, payload)
    _spawn_analyze(nid, mode="reset")
    return jsonify({"ok": True, "node_id": nid})


# ---------------- (Re-)analyze ------------------------------------------------

@scaffold_bp.route("/api/baza/projects/<pid>/scaffold/pcb_vision/analyze/<int:nid>",
                   methods=["POST"])
def pcb_vision_analyze(pid, nid):
    body = request.get_json(silent=True) or {}
    mode = body.get("mode") or "merge"
    if mode not in ("merge", "reset"):
        mode = "merge"
    eng = _engine()
    node = eng.get_node(nid)
    if not node or node.get("project_id") != pid:
        return jsonify({"ok": False, "error": "node_not_found"}), 404
    if node.get("node_type") != "pcb_vision":
        return jsonify({"ok": False, "error": "wrong_node_type"}), 400
    eng.update_node(nid, status="running")
    _spawn_analyze(nid, mode=mode)
    return jsonify({"ok": True, "node_id": nid, "mode": mode, "status": "running"})


# ---------------- Generate schematic from overlays ----------------------------

def _grid_from_overlays(overlays: list, canvas_w: int = 1200, canvas_h: int = 800) -> list:
    """Place components in the canvas preserving relative photo geometry."""
    out = []
    for ov in overlays:
        bbox = ov.get("bbox") or [0.1, 0.1, 0.2, 0.2]
        spid = ov.get("suggested_part_id")
        comp = None
        if spid:
            try:
                from core.baza_components_library import get_component
                comp = get_component(spid)
            except Exception:
                comp = None
        width  = (comp or {}).get("width", 120)
        height = (comp or {}).get("height", 80)
        x = int(bbox[0] * canvas_w)
        y = int(bbox[1] * canvas_h)
        out.append({
            "id": ov["id"],
            "part_id": spid,
            "label": ov.get("label", spid or "?"),
            "x": x, "y": y,
            "width": width, "height": height,
            "pins": (comp or {}).get("pins", []),
        })
    return out


def _best_guess_wires(components: list) -> list:
    """Heuristic-only wires: power/ground rails + sensor→MCU signal hops."""
    wires = []
    next_wid = 1

    def add(from_c, from_pin, to_c, to_pin, kind):
        nonlocal next_wid
        wires.append({
            "id": f"w_{next_wid}",
            "from": {"component": from_c, "pin": from_pin},
            "to":   {"component": to_c,   "pin": to_pin},
            "kind": kind,
            "auto_generated": True,
        })
        next_wid += 1

    # find first power source + first ground supplier
    def find_pin(c, kind, name_substrs):
        for p in c.get("pins") or []:
            if p.get("kind") == kind:
                return p["name"]
            n = (p.get("name") or "").lower()
            if any(s in n for s in name_substrs):
                return p["name"]
        return None

    def _pid(c):
        return (c.get("part_id") or "")

    power_src = next((c for c in components if _pid(c).startswith("power.")
                                              or _pid(c) == "usb-micro"
                                              or find_pin(c, "power", ["vcc", "vbus", "3v3", "5v"])),
                     None)
    mcus = [c for c in components if _pid(c).startswith("mcu.")
                                  or _pid(c).startswith("esp")
                                  or find_pin(c, "power", ["3v3", "vin", "5v"])]
    sensors = [c for c in components if _pid(c).startswith("sensor.")
                                     or _pid(c) in ("hc-sr04",)]

    # power → every component's VCC
    if power_src:
        ps = power_src["id"]
        ps_pin = find_pin(power_src, "power", ["vbus", "vcc", "5v", "3v3", "v+"]) or "VCC"
        for c in components:
            if c["id"] == ps: continue
            vcc = find_pin(c, "power", ["3v3", "vcc", "vin", "5v"])
            if vcc:
                add(ps, ps_pin, c["id"], vcc, "power")

    # ground bus: pick any GND-bearing component as the anchor
    gnd_anchor = next((c for c in components if find_pin(c, "ground", ["gnd", "ground"])),
                      None)
    if gnd_anchor:
        ga = gnd_anchor["id"]
        ga_pin = find_pin(gnd_anchor, "ground", ["gnd"]) or "GND"
        for c in components:
            if c["id"] == ga: continue
            g = find_pin(c, "ground", ["gnd", "ground"])
            if g:
                add(ga, ga_pin, c["id"], g, "ground")

    # signal: each sensor's first non-power/ground pin → next free GPIO on the first MCU
    if mcus and sensors:
        mcu = mcus[0]
        gpios = [p["name"] for p in (mcu.get("pins") or [])
                 if p.get("kind") == "gpio"]
        gi = 0
        for sn in sensors:
            for p in sn.get("pins") or []:
                if p.get("kind") in ("ground", "power"):
                    continue
                if gi >= len(gpios):
                    break
                add(sn["id"], p["name"], mcu["id"], gpios[gi], "signal")
                gi += 1
                break

    return wires


@scaffold_bp.route("/api/baza/projects/<pid>/scaffold/pcb_vision/generate_schematic/<int:nid>",
                   methods=["POST"])
def pcb_vision_generate_schematic(pid, nid):
    body = request.get_json(silent=True) or {}
    best_guess = bool(body.get("best_guess_wires"))
    eng = _engine()
    node = eng.get_node(nid)
    if not node or node.get("project_id") != pid:
        return jsonify({"ok": False, "error": "node_not_found"}), 404
    if node.get("node_type") != "pcb_vision":
        return jsonify({"ok": False, "error": "wrong_node_type"}), 400

    payload = json.loads(node.get("payload_json") or "{}")
    overlays = payload.get("overlays") or []
    if not overlays:
        return jsonify({"ok": False, "error": "no_overlays_yet"}), 400

    components = _grid_from_overlays(overlays)
    existing_schem = payload.get("schematic") or {}
    user_wires = [w for w in (existing_schem.get("wires") or [])
                  if not w.get("auto_generated")]
    wires = list(user_wires)
    if best_guess:
        wires.extend(_best_guess_wires(components))

    schem = {
        "components": components,
        "wires": wires,
        "notes": existing_schem.get("notes", ""),
    }
    payload["schematic"] = schem
    payload["best_guess_wires"] = best_guess
    eng.update_node(nid, payload_json=json.dumps(payload))
    return jsonify({"ok": True, "schematic": schem})


# ---------------- Image serving (re-checks vault lock on each fetch) ----------

@scaffold_bp.route("/api/baza/projects/<pid>/scaffold/pcb_vision/image/<int:nid>")
def pcb_vision_image(pid, nid):
    eng = _engine()
    node = eng.get_node(nid)
    if not node or node.get("project_id") != pid:
        abort(404)
    if node.get("node_type") != "pcb_vision":
        abort(404)
    payload = json.loads(node.get("payload_json") or "{}")
    p = Path(payload.get("image_path", ""))
    if not p.exists():
        abort(404)
    src = payload.get("image_source", "")
    if src == "datahub_private" and not _is_private_unlocked():
        abort(403)
    mime = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
    resp = send_from_directory(p.parent, p.name, mimetype=mime)
    if src == "upload":
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        resp.headers["Cache-Control"] = "no-cache"
    return resp


# ---------------- Data Hub thumbnail (privacy-gated, cached) ------------------

@scaffold_bp.route("/api/baza/projects/<pid>/scaffold/pcb_vision/datahub_thumb")
def pcb_vision_datahub_thumb(pid):
    """Serve a 200px JPEG thumbnail for any image path under the cloud root
    (or the private vault if unlocked). Cached on disk by path+mtime."""
    raw = request.args.get("path", "")
    is_private = request.args.get("private", "0") == "1"
    if not raw:
        abort(400)
    try:
        p = Path(raw).resolve()
    except OSError:
        abort(404)
    if not p.exists() or not p.is_file():
        abort(404)
    if p.suffix.lower() not in PCB_IMAGE_EXTS:
        abort(415)
    if is_private and not _is_private_unlocked():
        abort(403)

    # path safety: must be inside one of the configured browse roots
    allowed_roots: list[Path] = []
    for r in BROWSE_ROOTS:
        cfg = _resolve_browse_root(r["key"], pid)
        if cfg and cfg["resolved_path"].exists():
            try:
                allowed_roots.append(cfg["resolved_path"].resolve())
            except OSError:
                pass
    if not any(str(p).startswith(str(a) + os.sep) or str(p) == str(a)
               for a in allowed_roots):
        abort(403)

    import hashlib as _h
    thumb_dir = REPO_ROOT / "dashboard" / "artifacts" / ".pcb_thumb_cache"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    key = _h.md5(f"{p}|{p.stat().st_mtime}".encode()).hexdigest()[:16]
    cached = thumb_dir / f"{key}_200.jpg"
    if not cached.exists():
        try:
            try:
                import pillow_heif; pillow_heif.register_heif_opener()
            except ImportError:
                pass
            from PIL import Image, ImageOps
        except ImportError:
            abort(500)
        try:
            img = Image.open(p)
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGB")
            img.thumbnail((200, 200), Image.LANCZOS)
            img.save(cached, "JPEG", quality=82)
        except Exception:
            abort(500)
    resp = send_from_directory(thumb_dir, cached.name, mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


# ---------------- Data Hub image lister ---------------------------------------

def _resolve_browse_root(root_key: str, pid: str):
    cfg = next((r for r in BROWSE_ROOTS if r["key"] == root_key), None)
    if not cfg:
        return None
    if cfg.get("project_scoped"):
        path = REPO_ROOT / "dashboard" / "artifacts" / pid
    else:
        path = Path(cfg["path"])
    return {**cfg, "resolved_path": path}


def _walk_for_images(root: Path, max_depth: int | None, cap: int = 500,
                     follow_symlinks: bool = False) -> list[dict]:
    """Walk a directory tree collecting image files, depth-bounded."""
    items: list[dict] = []
    root_depth = len(root.parts)
    for dirpath, dirnames, files in os.walk(root, followlinks=follow_symlinks):
        depth = len(Path(dirpath).parts) - root_depth
        if max_depth is not None and depth >= max_depth:
            dirnames[:] = []
        # skip dotdirs and hidden
        dirnames[:] = [d for d in dirnames if not d.startswith(".")
                       and d not in ("venv", "__pycache__", "node_modules",
                                     ".private-inbound")]
        for name in files:
            p = Path(dirpath) / name
            if p.suffix.lower() not in PCB_IMAGE_EXTS:
                continue
            try:
                st = p.stat()  # follows symlinks (Photos/ entries are symlinks to Imports/)
            except OSError:
                continue
            try:
                rel = p.relative_to(root).as_posix()
            except ValueError:
                rel = name
            items.append({
                "path": str(p),
                "name": name,
                "rel_path": rel,
                "size": st.st_size,
                "mtime": int(st.st_mtime),
            })
        if len(items) >= cap:
            break
    return items


@scaffold_bp.route("/api/baza/projects/<pid>/scaffold/pcb_vision/datahub_roots")
def pcb_vision_datahub_roots(pid):
    """Return the list of browseable image roots (privacy-aware)."""
    unlocked = _is_private_unlocked()
    out = []
    for r in BROWSE_ROOTS:
        if r.get("hidden"):
            continue
        resolved = _resolve_browse_root(r["key"], pid)
        if not resolved:
            continue
        p = resolved["resolved_path"]
        if r["requires_unlock"] and not unlocked:
            out.append({"key": r["key"], "label": r["label"],
                        "available": False, "reason": "vault locked"})
            continue
        out.append({
            "key": r["key"], "label": r["label"],
            "available": p.exists() and p.is_dir(),
            "path": str(p),
        })
    return jsonify({"ok": True, "roots": out})


@scaffold_bp.route("/api/baza/projects/<pid>/scaffold/pcb_vision/datahub_list")
def pcb_vision_datahub_list(pid):
    """List image files in a given root (defaults to datahub for back-compat)."""
    root_key = request.args.get("root", "datahub")
    cfg = _resolve_browse_root(root_key, pid)
    if not cfg:
        return jsonify({"ok": False, "error": f"unknown root: {root_key}"}), 400
    if cfg.get("requires_unlock") and not _is_private_unlocked():
        return jsonify({"ok": False, "error": "vault locked"}), 403
    root_path = cfg["resolved_path"]
    if not root_path.exists() or not root_path.is_dir():
        return jsonify({"ok": True, "count": 0, "items": [], "root_path": str(root_path),
                        "note": "root does not exist"})
    items = _walk_for_images(root_path, cfg.get("max_depth"),
                             follow_symlinks=bool(cfg.get("follow_symlinks")))
    is_private = (root_key == "vault")
    for it in items:
        it["is_private"] = is_private
        it["root"] = root_key
    items.sort(key=lambda x: -x["mtime"])
    items = items[:500]
    return jsonify({"ok": True, "count": len(items), "root_path": str(root_path),
                    "root_key": root_key, "items": items})
