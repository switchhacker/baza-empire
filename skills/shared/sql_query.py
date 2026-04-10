#!/usr/bin/env python3
"""Run a read-only SQL query on baza_projects.db."""
import os, json, sqlite3
args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "dashboard", "baza_projects.db")
query = args.get("query", "")
if not query or any(w in query.upper() for w in ["DROP","DELETE","UPDATE","INSERT","ALTER","CREATE"]):
    print(json.dumps({"error": "Read-only queries only (SELECT)"}))
else:
    conn = sqlite3.connect(DB); conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(query).fetchall()]
        print(json.dumps({"rows": rows[:100], "count": len(rows)}))
    except Exception as e: print(json.dumps({"error": str(e)}))
    conn.close()
