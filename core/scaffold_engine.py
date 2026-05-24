"""Scaffold graph engine — CRUD, dependency checks, progress math, event bus."""
import json
import sqlite3
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

NODE_TYPES = {
    "root", "research", "decision", "hardware_component",
    "firmware", "software_module", "integration",
    "test", "deploy", "result", "manual_step",
}

DEFAULT_WEIGHTS = {
    "root": 0,
    "research": 1,
    "decision": 1,
    "hardware_component": 3,
    "firmware": 5,
    "software_module": 4,
    "integration": 4,
    "test": 2,
    "deploy": 2,
    "manual_step": 2,
    "result": 0,
}

_AGENT_BY_TYPE = {
    "research": "rex_smasher",
    "hardware_component": "rex_smasher",
    "decision": "claw_batto",
    "firmware": "phil_hass",
    "software_module": "phil_hass",
    "integration": "claw_batto",
    "test": "phil_hass",
    "deploy": "claw_batto",
    "manual_step": None,
    "root": None,
    "result": "claw_batto",
}


def default_agent_for(node_type):
    return _AGENT_BY_TYPE.get(node_type)


class _EventBus:
    """In-process pub/sub keyed by project_id. Used by SSE writers."""
    def __init__(self):
        self._lock = threading.Lock()
        self._subs = defaultdict(list)

    def subscribe(self, project_id, callback):
        with self._lock:
            self._subs[project_id].append(callback)

    def unsubscribe(self, project_id, callback):
        with self._lock:
            try:
                self._subs[project_id].remove(callback)
            except ValueError:
                pass

    def publish(self, project_id, event):
        with self._lock:
            subs = list(self._subs.get(project_id, []))
        for cb in subs:
            try:
                cb(event)
            except Exception:
                pass


event_bus = _EventBus()


class ScaffoldEngine:
    def __init__(self, db_path):
        self.db_path = str(db_path)

    def _con(self):
        con = sqlite3.connect(self.db_path, timeout=5)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout = 5000")
        return con

    # ---------------- Node CRUD ----------------

    def create_node(self, project_id, node_type, title, description="",
                    parent_id=None, weight=None, agent=None, payload=None,
                    status="pending"):
        if node_type not in NODE_TYPES:
            raise ValueError(f"unknown node_type: {node_type}")
        if weight is None:
            weight = DEFAULT_WEIGHTS.get(node_type, 1)
        depth = 0
        if parent_id:
            with self._con() as con:
                row = con.execute(
                    "SELECT depth FROM project_scaffold_nodes WHERE id=?",
                    (parent_id,)
                ).fetchone()
                if row:
                    depth = row["depth"] + 1
        payload_str = json.dumps(payload, default=str) if payload else None
        with self._con() as con:
            cur = con.execute("""
                INSERT INTO project_scaffold_nodes
                  (project_id, parent_id, node_type, title, description,
                   status, agent_assigned, payload_json, weight, depth)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (project_id, parent_id, node_type, title, description,
                  status, agent, payload_str, weight, depth))
            nid = cur.lastrowid
            con.commit()
        self.emit_event(project_id, node_id=nid, event_type="created",
                        actor=agent or "system",
                        payload={"node_type": node_type, "title": title,
                                 "parent_id": parent_id})
        return nid

    def get_node(self, node_id):
        with self._con() as con:
            row = con.execute(
                "SELECT * FROM project_scaffold_nodes WHERE id=?",
                (node_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_nodes(self, project_id):
        with self._con() as con:
            rows = con.execute(
                "SELECT * FROM project_scaffold_nodes WHERE project_id=? ORDER BY depth, id",
                (project_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def update_node(self, node_id, **fields):
        allowed = {"title", "description", "status", "weight", "payload_json",
                   "agent_assigned", "chosen_option", "auto_decided",
                   "started_at", "completed_at", "x", "y"}
        sets, vals = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            sets.append(f"{k}=?")
            vals.append(v)
        if not sets:
            return
        sets.append("updated_at=CURRENT_TIMESTAMP")
        vals.append(node_id)
        with self._con() as con:
            con.execute(
                f"UPDATE project_scaffold_nodes SET {', '.join(sets)} WHERE id=?",
                vals
            )
            con.commit()
        node = self.get_node(node_id)
        if node and "status" in fields:
            self.emit_event(node["project_id"], node_id=node_id,
                            event_type="status_changed",
                            actor="system",
                            payload={"new_status": fields["status"]})

    def delete_node(self, node_id):
        """Cascades to descendants."""
        node = self.get_node(node_id)
        if not node:
            return
        pid = node["project_id"]
        with self._con() as con:
            to_delete = [node_id]
            queue = [node_id]
            while queue:
                parent = queue.pop()
                rows = con.execute(
                    "SELECT id FROM project_scaffold_nodes WHERE parent_id=?",
                    (parent,)
                ).fetchall()
                for r in rows:
                    to_delete.append(r["id"])
                    queue.append(r["id"])
            con.executemany(
                "DELETE FROM project_scaffold_nodes WHERE id=?",
                [(i,) for i in to_delete]
            )
            con.executemany(
                "DELETE FROM project_scaffold_edges WHERE from_node=? OR to_node=?",
                [(i, i) for i in to_delete]
            )
            con.commit()
        self.emit_event(pid, node_id=node_id, event_type="deleted",
                        actor="system", payload={"cascade_count": len(to_delete)})

    # ---------------- Edges ----------------

    def add_edge(self, project_id, from_node, to_node, edge_type):
        with self._con() as con:
            con.execute("""
                INSERT INTO project_scaffold_edges
                  (project_id, from_node, to_node, edge_type)
                VALUES (?, ?, ?, ?)
            """, (project_id, from_node, to_node, edge_type))
            con.commit()

    def get_edges(self, project_id):
        with self._con() as con:
            rows = con.execute(
                "SELECT * FROM project_scaffold_edges WHERE project_id=?",
                (project_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    # ---------------- Dependency check ----------------

    def is_runnable(self, node_id):
        node = self.get_node(node_id)
        if not node or node["status"] != "pending":
            return False
        if node["parent_id"]:
            parent = self.get_node(node["parent_id"])
            if parent and parent["status"] not in ("in_progress", "done"):
                return False
        with self._con() as con:
            unfinished = con.execute("""
                SELECT 1 FROM project_scaffold_edges e
                JOIN project_scaffold_nodes n ON n.id = e.from_node
                WHERE e.to_node=? AND e.edge_type='depends_on' AND n.status != 'done'
                LIMIT 1
            """, (node_id,)).fetchone()
            if unfinished:
                return False
        return True

    def get_runnable_nodes(self, project_id, limit=20):
        with self._con() as con:
            rows = con.execute("""
                SELECT n.* FROM project_scaffold_nodes n
                LEFT JOIN project_scaffold_nodes p ON p.id = n.parent_id
                WHERE n.project_id = ?
                  AND n.status = 'pending'
                  AND (n.parent_id IS NULL OR p.status IN ('in_progress','done'))
                  AND NOT EXISTS (
                      SELECT 1 FROM project_scaffold_edges e
                      JOIN project_scaffold_nodes dep ON dep.id = e.from_node
                      WHERE e.to_node = n.id AND e.edge_type = 'depends_on'
                        AND dep.status != 'done'
                  )
                ORDER BY n.depth, n.id
                LIMIT ?
            """, (project_id, limit)).fetchall()
        return [dict(r) for r in rows]

    # ---------------- Progress ----------------

    def progress_pct(self, project_id):
        nodes = self.get_nodes(project_id)
        total = sum(n["weight"] for n in nodes)
        if total == 0:
            return 0
        done = sum(n["weight"] for n in nodes if n["status"] == "done")
        return int(round(100 * done / total))

    def has_star(self, project_id):
        nodes = self.get_nodes(project_id)
        result_nodes = [n for n in nodes if n["node_type"] == "result"]
        if not result_nodes:
            return False
        if not all(n["status"] == "done" for n in result_nodes):
            return False
        # If total weight is 0 (e.g. demo with all weight=0 nodes), result
        # completion alone constitutes a star.
        total = sum(n["weight"] for n in nodes)
        if total == 0:
            return True
        return self.progress_pct(project_id) == 100

    # ---------------- Decisions ----------------

    def decide(self, node_id, chosen_option, reason=""):
        self.update_node(node_id,
                         status="done",
                         chosen_option=chosen_option,
                         auto_decided=1,
                         completed_at=datetime.now(timezone.utc).isoformat())
        node = self.get_node(node_id)
        self.emit_event(node["project_id"], node_id=node_id, event_type="decided",
                        actor=node["agent_assigned"] or "system",
                        payload={"chosen": chosen_option, "reason": reason})

    def override_decision(self, node_id, chosen_option, reason=""):
        node = self.get_node(node_id)
        if not node:
            return
        with self._con() as con:
            deps = con.execute("""
                SELECT to_node FROM project_scaffold_edges
                WHERE from_node=? AND edge_type='depends_on'
            """, (node_id,)).fetchall()
        for d in deps:
            ddata = self.get_node(d["to_node"])
            if ddata and ddata["status"] in ("in_progress", "done", "failed"):
                self.update_node(d["to_node"], status="pending",
                                 started_at=None, completed_at=None)
        self.update_node(node_id,
                         status="overridden",
                         chosen_option=chosen_option,
                         auto_decided=0)
        self.emit_event(node["project_id"], node_id=node_id, event_type="overridden",
                        actor="user",
                        payload={"chosen": chosen_option, "reason": reason})

    # ---------------- Events ----------------

    def emit_event(self, project_id, node_id=None, event_type="note",
                   actor="system", payload=None):
        payload_str = json.dumps(payload, default=str) if payload else None
        with self._con() as con:
            cur = con.execute("""
                INSERT INTO project_scaffold_events
                  (project_id, node_id, event_type, actor, payload)
                VALUES (?, ?, ?, ?, ?)
            """, (project_id, node_id, event_type, actor, payload_str))
            event_id = cur.lastrowid
            con.commit()
        event = {
            "id": event_id,
            "project_id": project_id,
            "node_id": node_id,
            "event_type": event_type,
            "actor": actor,
            "payload": payload or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        event_bus.publish(project_id, event)
        return event_id

    def get_events(self, project_id, since_id=0, limit=200):
        with self._con() as con:
            rows = con.execute("""
                SELECT * FROM project_scaffold_events
                WHERE project_id=? AND id>?
                ORDER BY id ASC LIMIT ?
            """, (project_id, since_id, limit)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if d.get("payload"):
                try:
                    d["payload"] = json.loads(d["payload"])
                except Exception:
                    pass
            out.append(d)
        return out
