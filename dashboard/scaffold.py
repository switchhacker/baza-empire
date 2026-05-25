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

    # Auto-spawn a schematic node on first hardware BOM addition (one-shot per project)
    try:
        eng = _engine()
        import sqlite3 as _sq
        _con = _sq.connect(_db_path()); _con.row_factory = _sq.Row
        try:
            # Does a schematic node already exist for this project?
            has_schem = _con.execute(
                "SELECT 1 FROM project_scaffold_nodes WHERE project_id=? AND node_type='schematic' LIMIT 1",
                (pid,)
            ).fetchone()
            # Is this BOM row hardware-ish (matches a known component)?
            from core.baza_components_library import match_component
            is_hw = match_component(name) is not None
        finally:
            _con.close()
        if not has_schem and is_hw:
            # Find the root node to attach to (or use no parent if root missing)
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
            # Trigger the propose-schematic skill in-process (don't subprocess — we have the engine here)
            try:
                from core.baza_components_library import list_components, get_component
                # Inline a tiny version of the propose logic: collect BOM, match components, lay out grid, write payload
                cur = _sq.connect(_db_path())
                cur.row_factory = _sq.Row
                try:
                    bom_rows = cur.execute(
                        "SELECT * FROM project_bom WHERE project_id=?", (pid,)
                    ).fetchall()
                finally:
                    cur.close()
                components_out = []
                seen_ids = set()
                for i, b in enumerate(bom_rows[:16]):
                    matched = match_component(b["name"] or "")
                    if not matched:
                        continue
                    inst_id = f"u{len(components_out)+1}"
                    components_out.append({
                        "instance_id": inst_id,
                        "component_id": matched["id"],
                        "x": 60 + (len(components_out) % 4) * 250,
                        "y": 60 + (len(components_out) // 4) * 250,
                        "label": b["name"][:40],
                    })
                    seen_ids.add(matched["id"])
                # Simple wiring: power/ground rails to first MCU
                wires = []
                mcu = next((c for c in components_out if get_component(c["component_id"])["category"] == "mcu"), None)
                if mcu:
                    mcu_pins = {p["name"]: p for p in get_component(mcu["component_id"])["pins"]}
                    mcu_power = next((p["name"] for p in get_component(mcu["component_id"])["pins"] if p["kind"] == "power"), None)
                    mcu_gnd = next((p["name"] for p in get_component(mcu["component_id"])["pins"] if p["kind"] == "ground"), None)
                    gpio_pool = [p["name"] for p in get_component(mcu["component_id"])["pins"] if p["kind"] == "gpio"]
                    gpio_idx = 0
                    for c in components_out:
                        if c["instance_id"] == mcu["instance_id"]:
                            continue
                        comp_def = get_component(c["component_id"])
                        for p in comp_def["pins"]:
                            if p["kind"] == "ground" and mcu_gnd:
                                wires.append({"from": f"{mcu['instance_id']}.{mcu_gnd}",
                                             "to": f"{c['instance_id']}.{p['name']}", "color": "ground"})
                            elif p["kind"] == "power" and mcu_power:
                                wires.append({"from": f"{mcu['instance_id']}.{mcu_power}",
                                             "to": f"{c['instance_id']}.{p['name']}", "color": "power"})
                            elif p["kind"] in ("signal", "gpio", "pwm") and gpio_idx < len(gpio_pool):
                                wires.append({"from": f"{mcu['instance_id']}.{gpio_pool[gpio_idx]}",
                                             "to": f"{c['instance_id']}.{p['name']}", "color": "signal"})
                                gpio_idx += 1
                                break  # one signal wire per non-MCU component
                schematic = {
                    "components": components_out,
                    "wires": wires,
                    "notes": "Auto-proposed from BOM. Drag components to rearrange; click pins to wire."
                }
                import json as _json
                existing = eng.get_node(schem_id)
                payload = _json.loads(existing.get("payload_json") or "{}")
                payload["schematic"] = schematic
                eng.update_node(schem_id, payload_json=_json.dumps(payload, default=str))
                eng.emit_event(pid, node_id=schem_id, event_type="schematic_proposed",
                               actor="system", payload={"component_count": len(components_out)})
            except Exception as e:
                eng.emit_event(pid, node_id=schem_id, event_type="note", actor="system",
                               payload={"warning": f"auto-propose failed: {e}"})
    except Exception as e:
        # Never crash the BOM POST because of schematic logic
        print(f"[schematic auto-spawn] {e}", flush=True)

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
