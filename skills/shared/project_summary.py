#!/usr/bin/env python3
"""Generate a text summary of a project (phases, invoices, receipts)."""
import os, json, sqlite3

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
project_id = args.get("project_id", "")

DB = "/home/switchhacker/baza-empire/agent-framework-v3/dashboard/baza_projects.db"
try:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM tasks WHERE project_id = ?", (project_id,))
    tasks = [dict(r) for r in cur.fetchall()]
    summary = {
        "project_id": project_id,
        "total_tasks": len(tasks),
        "completed": sum(1 for t in tasks if t.get("status") == "done"),
        "in_progress": sum(1 for t in tasks if t.get("status") == "in_progress"),
        "pending": sum(1 for t in tasks if t.get("status") in ("pending", "todo")),
        "blocked": sum(1 for t in tasks if t.get("status") == "blocked"),
        "tasks": tasks[:20],
    }
    conn.close()
    print(json.dumps({"result": summary}))
except Exception as e:
    print(json.dumps({"error": str(e)}))
