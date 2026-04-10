#!/usr/bin/env python3
"""Calculate key performance indicators for the business."""
import os, json, sqlite3
args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "dashboard", "baza_projects.db")
conn = sqlite3.connect(DB); conn.row_factory = sqlite3.Row
rev = conn.execute("SELECT coalesce(sum(total),0) as t FROM ahb_invoices").fetchone()["t"]
exp = conn.execute("SELECT coalesce(sum(total),0) as t FROM ahb_receipts").fetchone()["t"]
labor = conn.execute("SELECT coalesce(sum(total),0) as t FROM ahb_payroll").fetchone()["t"]
projects = conn.execute("SELECT count(*) as c FROM ahb_projects").fetchone()["c"]
completed = conn.execute("SELECT count(*) as c FROM ahb_projects WHERE status='Completed'").fetchone()["c"]
clients = conn.execute("SELECT count(*) as c FROM ahb_clients").fetchone()["c"]
conn.close()
margin = ((rev - exp - labor) / rev * 100) if rev > 0 else 0
avg_project = rev / projects if projects > 0 else 0
print(json.dumps({"revenue": rev, "expenses": exp, "labor": labor, "profit": rev - exp - labor, "margin_pct": round(margin, 1), "projects": projects, "completed": completed, "completion_rate": round(completed/projects*100, 1) if projects else 0, "clients": clients, "avg_project_value": round(avg_project, 2)}))
