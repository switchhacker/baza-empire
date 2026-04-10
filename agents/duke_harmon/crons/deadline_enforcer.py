#!/usr/bin/env python3
"""Duke Harmon — Daily deadline enforcement. What's due today, this week, overdue."""
import os, sys, logging
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../.."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))
from agents.cron_helpers import *

logging.basicConfig(level=logging.INFO, format="%(asctime)s [DUKE-DEADLINES] %(message)s")

MODEL = "qwen2.5:14b"
AGENT_TOKEN = os.getenv("TELEGRAM_DUKE_HARMON", TELEGRAM_TOKEN)

def collect_data():
    conn = get_db()
    td = today()
    week_end = (datetime.date.today() + datetime.timedelta(days=7)).isoformat()

    overdue = conn.execute("SELECT title, assigned_to, due_date FROM tasks WHERE due_date < ? AND status NOT IN ('completed','done') AND due_date != '' ORDER BY due_date", (td,)).fetchall()
    due_today = conn.execute("SELECT title, assigned_to FROM tasks WHERE due_date = ? AND status NOT IN ('completed','done')", (td,)).fetchall()
    due_week = conn.execute("SELECT title, assigned_to, due_date FROM tasks WHERE due_date > ? AND due_date <= ? AND status NOT IN ('completed','done') ORDER BY due_date", (td, week_end)).fetchall()

    # Overdue invoices
    overdue_inv = conn.execute("SELECT project_name, total, client_name FROM ahb_invoices WHERE status='Overdue' LIMIT 5").fetchall()
    conn.close()

    data = f"DEADLINE REPORT — {td}\n\n"
    data += f"OVERDUE ({len(overdue)}):\n" + ("\n".join(f"  [{o[1] or '?'}] {o[0][:60]} — was due {o[2]}" for o in overdue) if overdue else "  None") + "\n\n"
    data += f"DUE TODAY ({len(due_today)}):\n" + ("\n".join(f"  [{d[1] or '?'}] {d[0][:60]}" for d in due_today) if due_today else "  None") + "\n\n"
    data += f"DUE THIS WEEK ({len(due_week)}):\n" + ("\n".join(f"  [{d[1] or '?'}] {d[0][:60]} — {d[2]}" for d in due_week) if due_week else "  None")
    if overdue_inv:
        data += "\n\nOVERDUE INVOICES:\n" + "\n".join(f"  {i[0][:40]} — ${i[1]:,.2f} ({i[2]})" for i in overdue_inv)
    return data

def main():
    log.info("Starting deadline enforcer...")
    data = collect_data()
    system = f"""You are Duke Harmon — Director of Project Management enforcing deadlines.
Daily deadline report for Serge. Plain text, no markdown. Max 20 lines.
Be aggressive about overdue items. Name names. Recommend actions.

{data}"""
    report = ollama_generate(MODEL, system, f"Deadline enforcement for {today()}")
    send_telegram(f"⏰ DEADLINE ENFORCER — {today()}\n\n{report}", AGENT_TOKEN)

if __name__ == "__main__":
    main()
