#!/usr/bin/env python3
"""Find duplicate or invalid records in AHB database."""
import os, json, sqlite3
args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "dashboard", "baza_projects.db")
conn = sqlite3.connect(DB); conn.row_factory = sqlite3.Row
issues = []
dupes = conn.execute("SELECT name, count(*) as c FROM ahb_clients GROUP BY lower(name) HAVING c > 1").fetchall()
for d in dupes: issues.append({"type": "duplicate_client", "name": d["name"], "count": d["c"]})
orphan_inv = conn.execute("SELECT count(*) as c FROM ahb_invoices WHERE project_id IS NULL OR project_id=''").fetchone()["c"]
if orphan_inv: issues.append({"type": "orphaned_invoices", "count": orphan_inv})
no_date = conn.execute("SELECT count(*) as c FROM ahb_receipts WHERE receipt_date IS NULL OR receipt_date=''").fetchone()["c"]
if no_date: issues.append({"type": "receipts_no_date", "count": no_date})
conn.close()
print(json.dumps({"issues": issues, "total_issues": len(issues)}))
