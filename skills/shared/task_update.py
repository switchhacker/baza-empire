#!/usr/bin/env python3
"""Update task status, assignee, priority, or details."""
import os, json, sqlite3

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
tid = args.get("id", "")
title_search = args.get("title", "")

db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../dashboard/baza_projects.db")
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row

if tid:
    row = conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
elif title_search:
    row = conn.execute("SELECT * FROM tasks WHERE title LIKE ?", (f"%{title_search}%",)).fetchone()
else:
    print(json.dumps({"error": "Provide id or title"}))
    conn.close()
    exit()

if not row:
    print(json.dumps({"error": "Task not found"}))
    conn.close()
    exit()

tid = row["id"]
fields, vals = [], []
for k in ["title","description","status","priority","assignee","project_id","deliverable"]:
    if k in args and k not in ("id",):
        fields.append(f"{k}=?")
        vals.append(args[k])

if fields:
    vals.append(tid)
    conn.execute(f"UPDATE tasks SET {','.join(fields)} WHERE id=?", vals)
    conn.commit()

conn.close()
print(json.dumps({"id": tid, "title": row["title"], "result": "updated"}))
