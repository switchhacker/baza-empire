#!/usr/bin/env python3
"""Generate expense report for a date range."""
import os, json, sqlite3

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
start_date = args.get("start_date", "2026-01-01")
end_date = args.get("end_date", "2026-12-31")
project_id = args.get("project_id", "")

DB = "/home/switchhacker/baza-empire/agent-framework-v3/dashboard/baza_projects.db"
try:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    query = "SELECT * FROM receipts WHERE date BETWEEN ? AND ?"
    params = [start_date, end_date]
    if project_id:
        query += " AND project_id = ?"
        params.append(project_id)
    query += " ORDER BY date"
    try:
        cur.execute(query, params)
        rows = [dict(r) for r in cur.fetchall()]
    except sqlite3.OperationalError:
        rows = []
    total = sum(float(r.get("amount", 0)) for r in rows)
    by_category = {}
    for r in rows:
        cat = r.get("category", "other")
        by_category.setdefault(cat, {"count": 0, "total": 0})
        by_category[cat]["count"] += 1
        by_category[cat]["total"] = round(by_category[cat]["total"] + float(r.get("amount", 0)), 2)
    conn.close()
    print(json.dumps({
        "period": f"{start_date} to {end_date}",
        "total_expenses": round(total, 2), "receipt_count": len(rows),
        "by_category": by_category, "items": rows[:100]
    }))
except Exception as e:
    print(json.dumps({"error": str(e)}))
