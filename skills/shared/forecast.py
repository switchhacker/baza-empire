#!/usr/bin/env python3
"""Simple revenue forecast based on historical monthly averages."""
import os, json, sqlite3
from datetime import datetime, timedelta
args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
months_ahead = args.get("months", 3)
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "dashboard", "baza_projects.db")
conn = sqlite3.connect(DB); conn.row_factory = sqlite3.Row
monthly = conn.execute("SELECT substr(created_at,1,7) as m, sum(total) as t FROM ahb_invoices GROUP BY m ORDER BY m").fetchall()
conn.close()
vals = [r["t"] for r in monthly if r["t"]]
avg = sum(vals) / len(vals) if vals else 0
now = datetime.now()
forecast = [{"month": (now + timedelta(days=30*i)).strftime("%Y-%m"), "projected": round(avg, 2)} for i in range(1, months_ahead + 1)]
print(json.dumps({"monthly_average": round(avg, 2), "forecast": forecast, "based_on_months": len(vals)}))
