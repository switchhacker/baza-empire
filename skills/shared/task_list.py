#!/usr/bin/env python3
"""List tasks with optional status/assignee filter."""
import os, json, sqlite3

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../dashboard/baza_projects.db")
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row

query = "SELECT * FROM tasks"
params = []
conditions = []
if args.get("status"):
    conditions.append("status=?")
    params.append(args["status"])
if args.get("assignee"):
    conditions.append("assignee LIKE ?")
    params.append(f"%{args['assignee']}%")
if conditions:
    query += " WHERE " + " AND ".join(conditions)
query += " ORDER BY created_at DESC LIMIT ?"
params.append(args.get("limit", 50))

rows = conn.execute(query, params).fetchall()
conn.close()
print(json.dumps({"count": len(rows), "tasks": [dict(r) for r in rows]}))
