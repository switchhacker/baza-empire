#!/usr/bin/env python3
"""Generate chart data — revenue/expenses over time by month."""
import os, json, sqlite3
args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "dashboard", "baza_projects.db")
metric = args.get("metric", "revenue")
conn = sqlite3.connect(DB); conn.row_factory = sqlite3.Row
if metric == "revenue":
    rows = conn.execute("SELECT substr(created_at,1,7) as month, sum(total) as total FROM ahb_invoices GROUP BY month ORDER BY month").fetchall()
elif metric == "expenses":
    rows = conn.execute("SELECT substr(receipt_date,1,7) as month, sum(total) as total FROM ahb_receipts WHERE receipt_date != '' GROUP BY month ORDER BY month").fetchall()
else: rows = []
conn.close()
print(json.dumps({"metric": metric, "data": [{"month": r["month"], "total": r["total"]} for r in rows]}))
