#!/usr/bin/env python3
"""List all Baza roadmap items with optional status/category filter."""
import os, json, sqlite3

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
status = args.get("status", "")
category = args.get("category", "")

db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../dashboard/baza_projects.db")
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row

query = "SELECT * FROM baza_roadmap"
params = []
conditions = []
if status:
    conditions.append("status=?")
    params.append(status)
if category:
    conditions.append("category=?")
    params.append(category)
if conditions:
    query += " WHERE " + " AND ".join(conditions)
query += " ORDER BY CASE status WHEN 'in_progress' THEN 0 WHEN 'planned' THEN 1 WHEN 'future' THEN 2 WHEN 'completed' THEN 3 END"

rows = conn.execute(query, params).fetchall()
conn.close()

items = [dict(r) for r in rows]
print(json.dumps({"count": len(items), "items": items}))
