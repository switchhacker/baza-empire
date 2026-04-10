#!/usr/bin/env python3
"""Get all projects, invoices, payments for a client."""
import os, json, sqlite3

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
client = args.get("client", "")

DB = "/home/switchhacker/baza-empire/agent-framework-v3/dashboard/baza_projects.db"
try:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    result = {"client": client, "projects": [], "invoices": [], "payments": []}
    for table, key in [("tasks", "assignee"), ("projects", "client"), ("invoices", "client"), ("payments", "client")]:
        try:
            cur.execute(f"SELECT * FROM {table} WHERE {key} LIKE ? OR name LIKE ?", (f"%{client}%", f"%{client}%"))
            rows = [dict(r) for r in cur.fetchall()]
            if table == "tasks":
                result["projects"] = rows
            else:
                result[table] = rows
        except sqlite3.OperationalError:
            continue
    conn.close()
    print(json.dumps(result))
except Exception as e:
    print(json.dumps({"error": str(e)}))
