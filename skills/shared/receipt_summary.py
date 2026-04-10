#!/usr/bin/env python3
"""Summarize receipts by category, date range, project."""
import os, json, sqlite3

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
project_id = args.get("project_id", "")
start_date = args.get("start_date", "")
end_date = args.get("end_date", "")
category = args.get("category", "")

DB = "/home/switchhacker/baza-empire/agent-framework-v3/dashboard/baza_projects.db"
try:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    query = "SELECT * FROM receipts WHERE 1=1"
    params = []
    if project_id:
        query += " AND project_id = ?"
        params.append(project_id)
    if category:
        query += " AND category LIKE ?"
        params.append(f"%{category}%")
    if start_date:
        query += " AND date >= ?"
        params.append(start_date)
    if end_date:
        query += " AND date <= ?"
        params.append(end_date)
    try:
        cur.execute(query, params)
        rows = [dict(r) for r in cur.fetchall()]
    except sqlite3.OperationalError:
        rows = []
    total = sum(float(r.get("amount", 0)) for r in rows)
    by_cat = {}
    for r in rows:
        c = r.get("category", "uncategorized")
        by_cat[c] = by_cat.get(c, 0) + float(r.get("amount", 0))
    conn.close()
    print(json.dumps({"count": len(rows), "total": round(total, 2), "by_category": by_cat, "receipts": rows[:50]}))
except Exception as e:
    print(json.dumps({"error": str(e)}))
