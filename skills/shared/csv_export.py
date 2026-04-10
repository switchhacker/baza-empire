#!/usr/bin/env python3
"""Export a database table to CSV format."""
import os, json, sqlite3, csv, io
args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "dashboard", "baza_projects.db")
table = args.get("table", "ahb_projects")
conn = sqlite3.connect(DB); conn.row_factory = sqlite3.Row
rows = conn.execute(f"SELECT * FROM {table} LIMIT 1000").fetchall()
conn.close()
if not rows: print(json.dumps({"csv": "", "count": 0}))
else:
    buf = io.StringIO(); w = csv.DictWriter(buf, fieldnames=dict(rows[0]).keys()); w.writeheader()
    for r in rows: w.writerow(dict(r))
    out = args.get("file")
    if out:
        with open(out, "w") as f: f.write(buf.getvalue())
        print(json.dumps({"file": out, "count": len(rows)}))
    else: print(json.dumps({"csv": buf.getvalue()[:5000], "count": len(rows)}))
