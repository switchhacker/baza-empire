#!/usr/bin/env python3
"""Phil Hass — Daily financial snapshot of AHBCO business."""
import os, sys, logging
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../.."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))
from agents.cron_helpers import *

logging.basicConfig(level=logging.INFO, format="%(asctime)s [PHIL-FINANCE] %(message)s")

MODEL = "o3-mini"  # Phil uses cloud model
AGENT_TOKEN = os.getenv("TELEGRAM_PHIL_HASS", TELEGRAM_TOKEN)

def collect_data():
    conn = get_db()
    year = str(datetime.date.today().year)

    # Revenue
    total_rev = conn.execute("SELECT COALESCE(SUM(total),0) FROM ahb_invoices WHERE status='Paid'").fetchone()[0]
    year_rev = conn.execute("SELECT COALESCE(SUM(total),0) FROM ahb_invoices WHERE status='Paid' AND year=?", (year,)).fetchone()[0]

    # Outstanding
    outstanding = conn.execute("SELECT COALESCE(SUM(total),0) FROM ahb_invoices WHERE status IN ('Approved','In Progress')").fetchone()[0]
    overdue = conn.execute("SELECT COALESCE(SUM(total),0) FROM ahb_invoices WHERE status='Overdue'").fetchone()[0]

    # Invoices by status
    inv_stats = dict(conn.execute("SELECT status, COUNT(*) FROM ahb_invoices GROUP BY status").fetchall())

    # Expenses (receipts)
    total_expenses = conn.execute("SELECT COALESCE(SUM(amount),0) FROM ahb_receipts WHERE year=?", (year,)).fetchone()[0]

    # Recent invoices
    recent = conn.execute("SELECT project_name, total, status, client_name FROM ahb_invoices ORDER BY created_at DESC LIMIT 5").fetchall()

    # Overdue debts
    debts = conn.execute("SELECT description, amount, due_date FROM ahb_debts WHERE status != 'paid' ORDER BY due_date LIMIT 5").fetchall()

    conn.close()

    data = f"""FINANCIAL SNAPSHOT — {today()}

REVENUE:
  All-time paid: ${total_rev:,.2f}
  {year} paid: ${year_rev:,.2f}
  Outstanding: ${outstanding:,.2f}
  Overdue: ${overdue:,.2f}

INVOICES BY STATUS: {dict(inv_stats)}

{year} EXPENSES (receipts): ${total_expenses:,.2f}

RECENT INVOICES:
""" + "\n".join(f"  {r[0][:50]} — ${r[1]:,.2f} [{r[2]}] ({r[3]})" for r in recent)

    if debts:
        data += "\n\nOUTSTANDING DEBTS:\n" + "\n".join(f"  {d[0][:50]} — ${d[1]:,.2f} due {d[2]}" for d in debts)

    return data

def main():
    log.info("Starting financial review...")
    data = collect_data()

    system = f"""You are Phil Hass — Director of Finance, Legal & Compliance at AHBCO LLC.
You're delivering your daily financial snapshot to Serge.

RULES:
- Plain text, no markdown
- Focus on cash flow: what's coming in, what's outstanding, what's overdue
- Flag any overdue invoices over $5,000
- Note profit margin if expenses vs revenue data available
- Recommend collections actions if needed
- Max 20 lines

{data}"""

    # Try cloud model first, fall back to local
    try:
        import urllib.request
        payload = json.dumps({
            "model": MODEL, "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": f"Daily financial report for {today()}"}
            ], "max_tokens": 600, "temperature": 0.5
        }).encode()
        litellm_key = os.getenv("LITELLM_MASTER_KEY", "baza-litellm-internal")
        req = urllib.request.Request("http://localhost:4000/v1/chat/completions",
                                     data=payload, headers={"Content-Type": "application/json",
                                                            "Authorization": f"Bearer {litellm_key}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            report = json.loads(r.read())["choices"][0]["message"]["content"]
    except Exception:
        report = ollama_generate("mistral-small:22b", system, f"Daily financial report for {today()}")

    save_artifact("proj-ahb123", f"financial_review_{today()}.md", f"# Financial Review — {today()}\n\n{report}")
    publish_event("phil_hass", "report_generated", {"type": "financial_review", "summary": report[:200]})
    send_telegram(f"💰 FINANCIAL REVIEW — {today()}\n\n{report}", AGENT_TOKEN)

if __name__ == "__main__":
    main()
