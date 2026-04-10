#!/usr/bin/env python3
"""Rex Valor — Daily lead follow-up check. Unresponded leads, pending callbacks."""
import os, sys, logging
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../.."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))
from agents.cron_helpers import *

logging.basicConfig(level=logging.INFO, format="%(asctime)s [REX-LEADS] %(message)s")

MODEL = "qwen2.5:14b"
AGENT_TOKEN = os.getenv("TELEGRAM_REX_VALOR", TELEGRAM_TOKEN)

def collect_data():
    conn = get_db()
    # Clients by status
    client_stats = dict(conn.execute("SELECT status, COUNT(*) FROM ahb_clients GROUP BY status").fetchall())
    # Leads (status = 'lead')
    leads = conn.execute("SELECT name, phone, email, source, created_at FROM ahb_clients WHERE status='lead' ORDER BY created_at DESC LIMIT 10").fetchall()
    # Recent events (calls, meetings)
    events = conn.execute("SELECT title, start_date, type FROM ahb_events WHERE type IN ('call','meeting','callback') ORDER BY start_date DESC LIMIT 8").fetchall()
    conn.close()

    data = f"""LEAD STATUS — {today()}

CLIENTS BY STATUS: {dict(client_stats)}

ACTIVE LEADS:
""" + ("\n".join(f"  {l[0]} — {l[1] or 'no phone'} | {l[2] or 'no email'} | src: {l[3] or '?'} | since {l[4][:10] if l[4] else '?'}" for l in leads) if leads else "  No active leads")
    if events:
        data += "\n\nRECENT CALLS/MEETINGS:\n" + "\n".join(f"  {e[0][:50]} — {e[1] or '?'} [{e[2]}]" for e in events)
    return data

def main():
    log.info("Starting lead follow-up...")
    data = collect_data()
    system = f"""You are Rex Valor — Director of Inbound Sales & Lead Operations at AHBCO LLC.
Daily lead follow-up report for Serge. Plain text, no markdown. Max 20 lines.
Focus on: leads needing follow-up, missed callbacks, conversion opportunities.
Prioritize by age — older leads need immediate action.

{data}"""
    report = ollama_generate(MODEL, system, f"Lead follow-up report for {today()}")
    save_artifact("proj-ahb123", f"lead_followup_{today()}.md", f"# Lead Follow-up — {today()}\n\n{report}")
    send_telegram(f"📞 LEAD FOLLOW-UP — {today()}\n\n{report}", AGENT_TOKEN)

if __name__ == "__main__":
    main()
