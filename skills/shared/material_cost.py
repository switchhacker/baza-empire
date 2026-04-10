#!/usr/bin/env python3
"""Look up material costs from receipts for a project."""
import os, json, sqlite3

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
project_id = args.get("project_id", "")
material_type = args.get("type", "")  # lumber, electrical, plumbing, etc.

DB = "/home/switchhacker/baza-empire/agent-framework-v3/dashboard/baza_projects.db"
try:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    query = "SELECT * FROM receipts WHERE category LIKE '%material%' OR category LIKE '%supply%'"
    params = []
    if project_id:
        query = "SELECT * FROM receipts WHERE project_id = ?"
        params = [project_id]
    if material_type:
        query += " AND (description LIKE ? OR category LIKE ?)"
        params.extend([f"%{material_type}%", f"%{material_type}%"])
    try:
        cur.execute(query, params)
        rows = [dict(r) for r in cur.fetchall()]
    except sqlite3.OperationalError:
        rows = []
    total = sum(float(r.get("amount", 0)) for r in rows)
    conn.close()
    print(json.dumps({"project_id": project_id, "material_type": material_type,
                       "total_cost": round(total, 2), "receipt_count": len(rows), "receipts": rows[:50]}))
except Exception as e:
    print(json.dumps({"error": str(e)}))
