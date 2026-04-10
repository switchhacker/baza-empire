#!/usr/bin/env python3
"""Calculate P&L for a project or date range."""
import os, json, sqlite3

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
project_id = args.get("project_id", "")
start_date = args.get("start_date", "2026-01-01")
end_date = args.get("end_date", "2026-12-31")

DB = "/home/switchhacker/baza-empire/agent-framework-v3/dashboard/baza_projects.db"
try:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    # Revenue from invoices
    revenue = 0
    try:
        q = "SELECT COALESCE(SUM(amount),0) as total FROM invoices WHERE date BETWEEN ? AND ?"
        p = [start_date, end_date]
        if project_id:
            q += " AND project_id = ?"
            p.append(project_id)
        cur.execute(q, p)
        revenue = float(cur.fetchone()["total"])
    except sqlite3.OperationalError:
        revenue = float(args.get("revenue", 0))
    # Expenses from receipts
    expenses = 0
    try:
        q = "SELECT COALESCE(SUM(amount),0) as total FROM receipts WHERE date BETWEEN ? AND ?"
        p = [start_date, end_date]
        if project_id:
            q += " AND project_id = ?"
            p.append(project_id)
        cur.execute(q, p)
        expenses = float(cur.fetchone()["total"])
    except sqlite3.OperationalError:
        expenses = float(args.get("expenses", 0))
    conn.close()
    profit = revenue - expenses
    margin = round((profit / revenue * 100), 1) if revenue > 0 else 0
    print(json.dumps({
        "period": f"{start_date} to {end_date}",
        "revenue": round(revenue, 2), "expenses": round(expenses, 2),
        "net_profit": round(profit, 2), "margin_pct": margin
    }))
except Exception as e:
    print(json.dumps({"error": str(e)}))
