#!/usr/bin/env python3
"""List all dashboard links."""
import os, json, sqlite3

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../dashboard/baza_projects.db")
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT * FROM baza_dash_links ORDER BY sort_order, created_at").fetchall()
conn.close()
items = [dict(r) for r in rows]
print(json.dumps({"count": len(items), "links": items}))
