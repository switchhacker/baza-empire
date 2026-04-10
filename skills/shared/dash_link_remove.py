#!/usr/bin/env python3
"""Remove a dashboard link by ID or title."""
import os, json, sqlite3

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
lid = args.get("id", "")
title_search = args.get("title", "")

db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../dashboard/baza_projects.db")
conn = sqlite3.connect(db)

if lid:
    conn.execute("DELETE FROM baza_dash_links WHERE id=?", (lid,))
elif title_search:
    conn.execute("DELETE FROM baza_dash_links WHERE title LIKE ?", (f"%{title_search}%",))
else:
    print(json.dumps({"error": "Provide id or title"}))
    conn.close()
    exit()

conn.commit()
conn.close()
print(json.dumps({"status": "removed", "target": lid or title_search}))
