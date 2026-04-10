#!/usr/bin/env python3
"""Look up client by name, phone, or email in ahb_clients DB."""
import os, json, sqlite3

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
query = args.get("query", "")
field = args.get("field", "name")  # name, phone, email

DB = "/home/switchhacker/baza-empire/agent-framework-v3/dashboard/baza_projects.db"
try:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    # Try multiple tables/columns
    results = []
    for table in ["clients", "ahb_clients"]:
        try:
            cur.execute(f"SELECT * FROM {table} WHERE {field} LIKE ? OR name LIKE ?", (f"%{query}%", f"%{query}%"))
            rows = [dict(r) for r in cur.fetchall()]
            results.extend(rows)
        except sqlite3.OperationalError:
            continue
    conn.close()
    print(json.dumps({"results": results, "count": len(results)}))
except Exception as e:
    print(json.dumps({"error": str(e)}))
