#!/usr/bin/env python3
"""Phil Hass — Daily financial snapshot of AHBCO business."""
import os, sys, logging
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../.."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))
from agents.cron_helpers import *

logging.basicConfig(level=logging.INFO, format="%(asctime)s [PHIL-FINANCE] %(message)s")

MODEL = "qwen2.5:14b"  # Local — matches phil_hass in config/agents.yaml and stays warm as LiteLLM fallback
CLOUD_MODEL = os.getenv("PHIL_FINANCE_CLOUD_MODEL", "")  # Opt-in; set to e.g. "claude-3-5-haiku" once keys are live
AGENT_TOKEN = os.getenv("TELEGRAM_PHIL_HASS", TELEGRAM_TOKEN)

def collect_data():
    conn = get_db()
    year = str(datetime.date.today().year)

    # Revenue (Paid vs everything else). ahb_invoices has no `year` column, so
    # derive year from paid_date/created_at.
    total_rev = conn.execute(
        "SELECT COALESCE(SUM(total),0) FROM ahb_invoices WHERE LOWER(status)='paid'"
    ).fetchone()[0]
    year_rev = conn.execute(
        "SELECT COALESCE(SUM(total),0) FROM ahb_invoices "
        "WHERE LOWER(status)='paid' AND substr(COALESCE(paid_date, created_at, ''),1,4)=?",
        (year,),
    ).fetchone()[0]

    # Outstanding / overdue
    outstanding = conn.execute(
        "SELECT COALESCE(SUM(total),0) FROM ahb_invoices "
        "WHERE LOWER(status) IN ('approved','in progress','in_progress','sent')"
    ).fetchone()[0]
    overdue = conn.execute(
        "SELECT COALESCE(SUM(total),0) FROM ahb_invoices WHERE LOWER(status)='overdue'"
    ).fetchone()[0]

    inv_stats = dict(conn.execute("SELECT status, COUNT(*) FROM ahb_invoices GROUP BY status").fetchall())

    # Expenses — prefer `year` column if populated, fall back to receipt_date prefix
    total_expenses = conn.execute(
        "SELECT COALESCE(SUM(COALESCE(NULLIF(total,0), amount)),0) FROM ahb_receipts "
        "WHERE (year=? OR substr(COALESCE(receipt_date,''),1,4)=?)",
        (year, year),
    ).fetchone()[0]

    # Recent invoices — join to projects/clients to get names
    recent = conn.execute(
        "SELECT COALESCE(p.title, i.invoice_number, i.id), i.total, i.status, "
        "       COALESCE(p.client_name, '') "
        "FROM ahb_invoices i "
        "LEFT JOIN ahb_projects p ON p.id = i.project_id "
        "ORDER BY i.created_at DESC LIMIT 5"
    ).fetchall()

    # Outstanding debts (schema: name, balance, payment_amount, due_date)
    debts = conn.execute(
        "SELECT name, COALESCE(NULLIF(balance,0), payment_amount, 0), due_date "
        "FROM ahb_debts WHERE COALESCE(balance,0) > 0 "
        "ORDER BY due_date LIMIT 5"
    ).fetchall()

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

    user_prompt = f"Daily financial report for {today()}"
    report = ""

    # Optional cloud pass (opt-in via PHIL_FINANCE_CLOUD_MODEL). Short timeout so
    # we fall back fast when cloud keys are unset or Gemini free-tier is exhausted.
    if CLOUD_MODEL:
        try:
            payload = json.dumps({
                "model": CLOUD_MODEL,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user_prompt}],
                "max_tokens": 600, "temperature": 0.5,
            }).encode()
            litellm_key = os.getenv("LITELLM_MASTER_KEY", "baza-litellm-internal")
            req = urllib.request.Request(
                "http://localhost:4000/v1/chat/completions", data=payload,
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {litellm_key}"})
            with urllib.request.urlopen(req, timeout=30) as r:
                body = json.loads(r.read())
                if body.get("model", "").startswith("ollama/"):
                    # LiteLLM fell back to local — let our own local path handle it cleanly
                    log.info(f"Cloud ({CLOUD_MODEL}) fell back to {body.get('model')}; running local pass directly")
                else:
                    report = body["choices"][0]["message"]["content"]
        except Exception as e:
            log.warning(f"Cloud LLM ({CLOUD_MODEL}) unavailable: {e}")

    # Local Ollama pass — primary path
    if not report:
        report = ollama_generate(MODEL, system, user_prompt, max_tokens=600)

    # If every LLM path failed, still deliver the raw data to Serge — the
    # structured snapshot is useful on its own.
    if not report or report.startswith("(LLM unavailable"):
        log.warning(f"All LLM paths failed, sending raw data. Reason: {report or 'empty'}")
        report = "LLM commentary unavailable — raw snapshot below.\n\n" + data

    save_artifact("proj-ahb123", f"financial_review_{today()}.md", f"# Financial Review — {today()}\n\n{report}")
    publish_event("phil_hass", "report_generated", {"type": "financial_review", "summary": report[:200]})
    send_telegram(f"💰 FINANCIAL REVIEW — {today()}\n\n{report}", AGENT_TOKEN)
    log_activity("phil_hass", f"Phil completed daily financial review for {today()}", task_type="financial_review")

if __name__ == "__main__":
    main()
