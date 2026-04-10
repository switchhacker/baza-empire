#!/usr/bin/env python3
"""Analyze revenue/expense trends over months."""
import os, json, sqlite3
args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "dashboard", "baza_projects.db")
conn = sqlite3.connect(DB); conn.row_factory = sqlite3.Row
rev = conn.execute("SELECT substr(created_at,1,7) as m, sum(total) as t FROM ahb_invoices GROUP BY m ORDER BY m").fetchall()
exp = conn.execute("SELECT substr(receipt_date,1,7) as m, sum(total) as t FROM ahb_receipts WHERE receipt_date!='' GROUP BY m ORDER BY m").fetchall()
conn.close()
rev_data = {r["m"]: r["t"] for r in rev}; exp_data = {r["m"]: r["t"] for r in exp}
months = sorted(set(list(rev_data.keys()) + list(exp_data.keys())))
trend = [{"month": m, "revenue": rev_data.get(m, 0), "expenses": exp_data.get(m, 0), "profit": rev_data.get(m, 0) - exp_data.get(m, 0)} for m in months]
print(json.dumps({"trend": trend, "months": len(months)}))
