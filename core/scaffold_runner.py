"""Continuous worker — finds runnable scaffold nodes and dispatches them.

Run as a systemd timer (every 30s). For each active+unpaused project,
finds runnable nodes (limit 20), assigns an agent by node_type, marks
in_progress, and inserts a row into the tasks table for the agent to pick up.
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def _db_path():
    return os.environ.get("BAZA_PROJECTS_DB",
                          str(REPO / "dashboard" / "baza_projects.db"))


def get_active_unpaused_projects(db_path):
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute("""
            SELECT DISTINCT p.id FROM projects p
            JOIN project_scaffold_nodes n ON n.project_id = p.id
            WHERE COALESCE(p.scaffold_paused, 0) = 0
              AND n.status IN ('pending', 'in_progress')
        """).fetchall()
        return [r[0] for r in rows]
    finally:
        con.close()


def _task_description_for(node, project_id):
    payload = node.get("payload_json") or "{}"
    parent_id = node.get("parent_id")
    return f"""You are working on Baza scaffold node {node['id']} in project {project_id}.

Node type: {node['node_type']}
Title: {node['title']}
Description: {node.get('description', '')}
Payload: {payload}
Parent: {parent_id}

When finished, you MUST end your response with:
##SKILL:scaffold_complete_node{{"node_id": {node['id']}, "result": "..."}}##

For research nodes: call ##SKILL:web_search{{...}}##, summarize 3-5 sources, pick one.
For decision nodes: list alternatives, pick the best, call scaffold_complete_node with `decision` set.
For hardware_component nodes: call ##SKILL:scaffold_add_bom{{...}}## with the chosen part, then complete.
For firmware / software_module nodes: write code into artifacts/scaffold/{node['id']}/ via the file tool, list paths in artifacts.

If blocked, end with ##SKILL:scaffold_complete_node{{"node_id": {node['id']}, "result": "blocked", "reason": "..."}}##
"""


def tick_project(project_id, db_path=None):
    """Process one project tick. Returns list of node IDs started."""
    db_path = db_path or _db_path()
    from core.scaffold_engine import ScaffoldEngine, default_agent_for

    # Honor pause flag
    con = sqlite3.connect(db_path)
    try:
        row = con.execute("SELECT scaffold_paused FROM projects WHERE id=?",
                          (project_id,)).fetchone()
        if row and row[0]:
            return []
    finally:
        con.close()

    eng = ScaffoldEngine(db_path)
    started = []
    runnable = eng.get_runnable_nodes(project_id, limit=20)
    for n in runnable:
        if n["node_type"] == "manual_step":
            continue
        agent = n.get("agent_assigned") or default_agent_for(n["node_type"])
        if not agent:
            continue
        eng.update_node(n["id"],
                        status="in_progress",
                        agent_assigned=agent,
                        started_at=datetime.now(timezone.utc).isoformat())
        eng.emit_event(project_id, node_id=n["id"], event_type="started",
                       actor=agent)
        try:
            con = sqlite3.connect(db_path)
            con.execute("""
                INSERT INTO tasks
                  (project_id, title, description, assigned_to, status, priority)
                VALUES (?, ?, ?, ?, 'pending', 5)
            """, (project_id,
                  f"[scaffold #{n['id']}] {n['title']}",
                  _task_description_for(n, project_id),
                  agent))
            con.commit()
        finally:
            con.close()
        started.append(n["id"])
    return started


def tick_all(db_path=None):
    db_path = db_path or _db_path()
    total = []
    for pid in get_active_unpaused_projects(db_path):
        try:
            total.extend(tick_project(pid, db_path=db_path))
        except Exception as e:
            print(f"[scaffold-runner] tick failed for {pid}: {e}", file=sys.stderr)
    return total


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true",
                        help="single tick then exit (systemd default)")
    parser.add_argument("--project", help="only run this project")
    args = parser.parse_args()
    # Keep the skill manifest fresh — cheap mtime check, rebuilds only on change.
    try:
        from core import skill_registry
        skill_registry.build_if_stale()
    except Exception as e:
        print(f"[scaffold-runner] manifest refresh skipped: {e}", file=sys.stderr)
    if args.project:
        result = tick_project(args.project)
    else:
        result = tick_all()
    print(json.dumps({"started": result}))


if __name__ == "__main__":
    main()
