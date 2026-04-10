#!/usr/bin/env python3
"""Generate a weekly business summary report."""
import os, json, sqlite3
from datetime import datetime, timedelta
args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "dashboard", "baza_projects.db")
conn = sqlite3.connect(DB); conn.row_factory = sqlite3.Row
end = datetime.now().strftime("%Y-%m-%d")
start = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
inv = conn.execute("SELECT count(*) as c, coalesce(sum(total),0) as t FROM ahb_invoices WHERE created_at >= ?", (start,)).fetchone()
rec = conn.execute("SELECT count(*) as c, coalesce(sum(total),0) as t FROM ahb_receipts WHERE receipt_date >= ? AND receipt_date <= ?", (start, end)).fetchone()
events = conn.execute("SELECT count(*) as c FROM ahb_events WHERE date >= ? AND date <= ?", (start, end)).fetchone()["c"]
conn.close()
print(json.dumps({"report": f"Weekly Report {start} to {end}\nInvoices: {inv['c']} (${inv['t']:,.2f})\nReceipts: {rec['c']} (${rec['t']:,.2f})\nEvents: {events}", "start": start, "end": end}))
