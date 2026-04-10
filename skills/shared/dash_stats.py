#!/usr/bin/env python3
"""Get dashboard database statistics — row counts for all tables."""
import os, json, sqlite3

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../dashboard/baza_projects.db")
conn = sqlite3.connect(db)

tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
stats = {}
total = 0
for t in tables:
    count = conn.execute(f"SELECT count(*) FROM [{t}]").fetchone()[0]
    stats[t] = count
    total += count
conn.close()

print(json.dumps({"tables": len(tables), "total_rows": total, "breakdown": stats}))
