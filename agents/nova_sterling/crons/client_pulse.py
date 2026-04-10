#!/usr/bin/env python3
"""Nova Sterling — Daily client satisfaction review."""
import os, sys, logging
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../.."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))
from agents.cron_helpers import *

logging.basicConfig(level=logging.INFO, format="%(asctime)s [NOVA-CLIENTS] %(message)s")

MODEL = "qwen2.5:14b"
AGENT_TOKEN = os.getenv("TELEGRAM_NOVA_STERLING", TELEGRAM_TOKEN)

def collect_data():
    conn = get_db()
    # Client overview
    total = conn.execute("SELECT count(*) FROM ahb_clients").fetchone()[0]
    active = conn.execute("SELECT count(*) FROM ahb_clients WHERE status='active'").fetchone()[0]
    leads = conn.execute("SELECT count(*) FROM ahb_clients WHERE status='lead'").fetchone()[0]

    # Clients with active projects
    active_clients = conn.execute("""
        SELECT DISTINCT c.name, c.phone, p.title, p.status
        FROM ahb_clients c JOIN ahb_projects p ON c.id = p.client_id
        WHERE p.status IN ('In Progress','Planning')
        ORDER BY c.name LIMIT 10""").fetchall()

    # Recent chat messages
    chats = conn.execute("SELECT visitor_name, message FROM ahb_chat_messages ORDER BY created_at DESC LIMIT 5").fetchall()

    # Overdue invoices (unhappy clients)
    overdue = conn.execute("""
        SELECT c.name, i.total, i.project_name FROM ahb_invoices i
        LEFT JOIN ahb_clients c ON i.client_name = c.name
        WHERE i.status = 'Overdue' LIMIT 5""").fetchall()
    conn.close()

    data = f"""CLIENT PULSE — {today()}

CLIENTS: {total} total | {active} active | {leads} leads

CLIENTS WITH ACTIVE PROJECTS:
""" + ("\n".join(f"  {c[0]} — {c[2][:40]} [{c[3]}]" for c in active_clients) if active_clients else "  None")
    if chats:
        data += "\n\nRECENT CHAT MESSAGES:\n" + "\n".join(f"  {c[0] or 'visitor'}: {(c[1] or '')[:60]}" for c in chats)
    if overdue:
        data += "\n\nCLIENTS WITH OVERDUE INVOICES (potential friction):\n" + "\n".join(f"  {o[0] or '?'} — ${o[1]:,.2f} ({o[2][:40]})" for o in overdue)
    return data

def main():
    log.info("Starting client pulse...")
    data = collect_data()
    system = f"""You are Nova Sterling — Director of Client Relations at AHBCO LLC.
Daily client satisfaction review for Serge. Plain text, no markdown. Max 20 lines.
Focus on: who needs attention, potential issues, follow-up opportunities.
Flag clients with overdue invoices — payment friction hurts relationships.

{data}"""
    report = ollama_generate(MODEL, system, f"Client pulse report for {today()}")
    save_artifact("proj-ahb123", f"client_pulse_{today()}.md", f"# Client Pulse — {today()}\n\n{report}")
    send_telegram(f"💬 CLIENT PULSE — {today()}\n\n{report}", AGENT_TOKEN)

if __name__ == "__main__":
    main()
