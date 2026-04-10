#!/usr/bin/env python3
"""Export a database table to JSON format."""
import os, json, sqlite3
args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "dashboard", "baza_projects.db")
table = args.get("table", "ahb_projects")
conn = sqlite3.connect(DB); conn.row_factory = sqlite3.Row
rows = [dict(r) for r in conn.execute(f"SELECT * FROM {table} LIMIT 500").fetchall()]
conn.close()
out = args.get("file")
if out:
    with open(out, "w") as f: json.dump(rows, f, indent=2, default=str)
    print(json.dumps({"file": out, "count": len(rows)}))
else: print(json.dumps({"data": rows[:20], "count": len(rows)}))
