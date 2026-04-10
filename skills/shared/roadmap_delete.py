#!/usr/bin/env python3
"""Delete a roadmap item by ID or title."""
import os, json, sqlite3

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
rid = args.get("id", "")
title_search = args.get("title", "")

db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../dashboard/baza_projects.db")
conn = sqlite3.connect(db)

if rid:
    conn.execute("DELETE FROM baza_roadmap WHERE id=?", (rid,))
elif title_search:
    conn.execute("DELETE FROM baza_roadmap WHERE title LIKE ?", (f"%{title_search}%",))
else:
    print(json.dumps({"error": "Provide id or title"}))
    conn.close()
    exit()

conn.commit()
conn.close()
print(json.dumps({"status": "deleted", "target": rid or title_search}))
