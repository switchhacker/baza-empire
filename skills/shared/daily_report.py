#!/usr/bin/env python3
"""Generate a daily business summary report."""
import os, json, sqlite3
from datetime import datetime
args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "dashboard", "baza_projects.db")
conn = sqlite3.connect(DB); conn.row_factory = sqlite3.Row
today = datetime.now().strftime("%Y-%m-%d")
projects = conn.execute("SELECT count(*) as c FROM ahb_projects").fetchone()["c"]
invoices_paid = conn.execute("SELECT count(*) as c, coalesce(sum(total),0) as t FROM ahb_invoices WHERE status='Paid'").fetchone()
receipts_today = conn.execute("SELECT count(*) as c, coalesce(sum(total),0) as t FROM ahb_receipts WHERE receipt_date=?", (today,)).fetchone()
events_today = conn.execute("SELECT count(*) as c FROM ahb_events WHERE date=?", (today,)).fetchone()["c"]
conn.close()
report = f"Daily Report — {today}\nProjects: {projects}\nPaid Invoices: {invoices_paid['c']} (${invoices_paid['t']:,.2f})\nReceipts Today: {receipts_today['c']} (${receipts_today['t']:,.2f})\nEvents Today: {events_today}"
print(json.dumps({"report": report, "date": today}))
