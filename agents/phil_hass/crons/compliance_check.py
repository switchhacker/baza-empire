#!/usr/bin/env python3
"""Phil Hass — Weekly compliance review (licenses, insurance, tax deadlines)."""
import os, sys, logging
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../.."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))
from agents.cron_helpers import *

logging.basicConfig(level=logging.INFO, format="%(asctime)s [PHIL-COMPLIANCE] %(message)s")
AGENT_TOKEN = os.getenv("TELEGRAM_PHIL_HASS", TELEGRAM_TOKEN)

def collect_data():
    conn = get_db()
    # Tax requirements
    tax_reqs = conn.execute(
        "SELECT title, due_date, completed, details FROM ahb_tax_requirements ORDER BY due_date"
    ).fetchall()
    # Active projects without proper docs
    projects = conn.execute(
        "SELECT title, status, COALESCE(NULLIF(location,''), address) "
        "FROM ahb_projects WHERE status IN ('In Progress','Planning','active','in_progress') "
        "ORDER BY created_at DESC LIMIT 10"
    ).fetchall()
    # Employee count
    try:
        emp_count = conn.execute("SELECT count(*) FROM ahb_employees").fetchone()[0]
    except Exception:
        emp_count = 0
    conn.close()

    tax_lines = "\n".join(
        f"  {r[0]} — due {r[1]} [{'done' if r[2] else 'open'}] {r[3] or ''}"
        for r in tax_reqs
    ) if tax_reqs else "  No tax requirements tracked"
    data = f"COMPLIANCE DATA — Week of {today()}\n\nTAX REQUIREMENTS:\n{tax_lines}"
    data += f"\n\nACTIVE PROJECTS ({len(projects)}):\n" + "\n".join(
        f"  {(p[0] or 'untitled')[:50]} [{p[1]}] @ {p[2] or 'no location'}" for p in projects
    )
    data += f"\n\nEMPLOYEES ON RECORD: {emp_count}"
    data += f"\n\nPA HIC LICENSE: Required for residential work over $500 in Pennsylvania"
    data += f"\nWORKERS COMP: Required if employees on payroll"
    data += f"\nGENERAL LIABILITY: Required for all active projects"
    return data

def main():
    log.info("Starting compliance check...")
    data = collect_data()
    system = f"""You are Phil Hass — Director of Finance, Legal & Compliance at AHBCO LLC.
Weekly compliance review for Serge. Plain text, no markdown. Max 20 lines.
Flag anything that's overdue, expiring soon, or missing.
Check: PA HIC license, insurance, workers comp, contractor agreements, tax deadlines.

{data}"""
    report = ollama_generate("mistral-small:22b", system, f"Weekly compliance report for week of {today()}")
    save_artifact("proj-ahb123", f"compliance_{today()}.md", f"# Compliance Check — {today()}\n\n{report}")
    send_report("compliance_check", f"⚖️ WEEKLY COMPLIANCE — {today()}\n\n{report}", priority="fyi", delta_key="compliance_check", token=AGENT_TOKEN)
    log_activity("phil_hass", f"Phil completed weekly compliance check for {today()}", task_type="compliance_check")

if __name__ == "__main__":
    with cron_run("compliance_check"):
        main()
