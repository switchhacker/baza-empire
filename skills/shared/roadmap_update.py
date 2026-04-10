#!/usr/bin/env python3
"""Update a roadmap item status, details, or start/complete it."""
import os, json, sqlite3, datetime

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
rid = args.get("id", "")
title_search = args.get("title", "")

db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../dashboard/baza_projects.db")
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row

# Find by ID or title search
if rid:
    row = conn.execute("SELECT * FROM baza_roadmap WHERE id=?", (rid,)).fetchone()
elif title_search:
    row = conn.execute("SELECT * FROM baza_roadmap WHERE title LIKE ?", (f"%{title_search}%",)).fetchone()
else:
    print(json.dumps({"error": "Provide id or title to find the roadmap item"}))
    exit()

if not row:
    print(json.dumps({"error": "Roadmap item not found"}))
    conn.close()
    exit()

rid = row["id"]
fields, vals = [], []
for k in ["title","description","status","priority","category","assigned_agent","target_date","notes"]:
    if k in args and k not in ("id",):
        fields.append(f"{k}=?")
        vals.append(args[k])

now = datetime.datetime.now().isoformat()
if args.get("status") == "in_progress" and not row["started_at"]:
    fields.append("started_at=?")
    vals.append(now)
if args.get("status") == "completed":
    fields.append("completed_at=?")
    vals.append(now)

fields.append("updated_at=?")
vals.append(now)
vals.append(rid)

if len(fields) > 1:
    conn.execute(f"UPDATE baza_roadmap SET {','.join(fields)} WHERE id=?", vals)
    conn.commit()

conn.close()
print(json.dumps({"id": rid, "title": row["title"], "status": args.get("status", row["status"]), "result": "updated"}))
