#!/usr/bin/env python3
"""
Phil Hass — daily overdue-invoice reminder drafts (Task 14 of the
cron-improvements plan, item 23).

Finds invoices that are unpaid and past their due date, drafts a
professional, friendly reminder email per invoice via a local Ollama model,
and hands each draft to Serge as an approve-to-send suggestion card
(skills/shared/suggest_action.py) -- this cron NEVER sends anything to a
client itself. Deduped to at most one suggestion per invoice per 7 days via
core.cron_health_db.should_alert (checked BEFORE the LLM call, so a
still-in-window invoice never costs a draft). The business DB
(dashboard/baza_projects.db) is read-only for this script -- it never
INSERTs/UPDATEs/DELETEs ahb_invoices or ahb_projects; the only writes are
cron_health.db's own dedup/heartbeat bookkeeping.

Standalone-executable (`venv/bin/python agents/phil_hass/crons/invoice_followup.py
[--dry-run]`). `main(now=None, dry_run=False)` is the testable entry point
and has no import-time side effects. `--dry-run` builds and logs/prints
every draft + suggestion that *would* be sent, without invoking
suggest_action.py (no Telegram card, no subprocess) -- safe to run live.
"""
import datetime
import logging
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../.."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))
from agents.cron_helpers import *  # noqa: F401,F403 -- get_db, cron_run, ollama_generate,
# log, today, now, FRAMEWORK_DIR, TELEGRAM_TOKEN (house style for every cron in this repo)

from core import cron_health_db as chdb

logging.basicConfig(level=logging.INFO, format="%(asctime)s [PHIL-INVFU] %(message)s")

CRON_NAME = "invoice_followup"
MODEL = "qwen2.5:14b"
AGENT_ID = "phil_hass"
AGENT_TOKEN = os.getenv("TELEGRAM_PHIL_HASS", TELEGRAM_TOKEN)

RENOTIFY_HOURS = 168  # 1 per invoice per 7 days, per the brief
DEDUP_PREFIX = "invfu:"

SUGGEST_ACTION_SCRIPT = os.path.join(FRAMEWORK_DIR, "skills", "shared", "suggest_action.py")
SUGGEST_ACTION_TIMEOUT = 330  # suggest_action.py's own wait_for_reply defaults to 300s

MIN_DRAFT_CHARS = 20

# Statuses that mean "nothing to chase" -- compared case-insensitively since
# real data has 'Paid'/'paid' drift (see task-14-report.md schema notes).
EXCLUDED_STATUSES = ("paid", "draft", "void")

# Invoice -> project -> client join for name/email, per the brief. Every
# level (ahb_invoices, ahb_projects, ahb_clients) carries its own
# name/email columns in the real DB, often blank at the invoice/project
# level -- COALESCE prefers the most specific non-empty value and falls
# back down the chain.
OVERDUE_QUERY = """
    SELECT
        i.id                AS id,
        i.invoice_number    AS invoice_number,
        i.due_date          AS due_date,
        i.total             AS total,
        i.amount_due        AS amount_due,
        i.status            AS status,
        i.project_id        AS project_id,
        i.client_id         AS client_id,
        COALESCE(NULLIF(i.client_name, ''), NULLIF(p.client_name, ''), c.name, '')  AS client_name,
        COALESCE(NULLIF(i.client_email, ''), NULLIF(p.client_email, ''), c.email, '') AS client_email,
        COALESCE(NULLIF(i.project_name, ''), p.title, '') AS project_name
    FROM ahb_invoices i
    LEFT JOIN ahb_projects p ON p.id = i.project_id
    LEFT JOIN ahb_clients c ON c.id = COALESCE(NULLIF(i.client_id, ''), p.client_id)
    WHERE LOWER(COALESCE(i.status, '')) NOT IN ({placeholders})
      AND i.due_date IS NOT NULL AND i.due_date != ''
      AND i.due_date < ?
      AND COALESCE(i.amount_due, i.total, 0) > 0
    ORDER BY i.due_date ASC
""".format(placeholders=",".join("?" for _ in EXCLUDED_STATUSES))


def _get_overdue_invoices(conn, today_str):
    """Read-only SELECT against ahb_invoices (joined to ahb_projects/ahb_clients
    for name+email). Never writes. Returns a list of sqlite3.Row (possibly
    empty); query failures are logged and degrade to an empty list rather
    than raising, matching this file's cron-body error style."""
    try:
        params = tuple(EXCLUDED_STATUSES) + (today_str,)
        return conn.execute(OVERDUE_QUERY, params).fetchall()
    except Exception as e:
        log.error(f"invoice_followup: overdue query failed: {e}")
        return []


def _row_to_invoice(row, when):
    """Normalize a joined ahb_invoices row + the run's `when` into a plain
    dict with the derived fields (`amount`, `days_overdue`) the rest of this
    module works with."""
    amount_due = row["amount_due"]
    amount = amount_due if amount_due is not None else (row["total"] or 0)
    due_date = row["due_date"] or ""
    days_overdue = None
    try:
        due = datetime.date.fromisoformat(due_date[:10])
        days_overdue = (when.date() - due).days
    except ValueError:
        days_overdue = None
    return {
        "id": row["id"],
        "invoice_number": row["invoice_number"] or row["id"],
        "due_date": due_date,
        "amount": amount,
        "status": row["status"],
        "client_name": row["client_name"] or "",
        "client_email": row["client_email"] or "",
        "project_name": row["project_name"] or "",
        "days_overdue": days_overdue,
    }


# ── LLM draft ─────────────────────────────────────────────────────────────

def _build_draft_prompt(invoice):
    system = (
        "You are Phil Hass, Director of Finance, Legal & Compliance at AHBCO LLC "
        "(All Home Building Co), drafting a payment reminder email to a client on "
        "an overdue invoice.\n"
        "RULES:\n"
        "- Professional, friendly, courteous tone -- this is a gentle reminder, not a demand.\n"
        "- Reference the invoice number, the amount owed, and how many days it is past due.\n"
        "- Do NOT include any legal threats, liens, interest charges, collections, or legal "
        "action language.\n"
        "- Plain email body only: no subject line, no markdown, no bracket placeholders.\n"
        "- Keep it under 150 words.\n"
        "- Sign off as Phil Hass, AHBCO LLC."
    )
    user = (
        f"Draft a reminder email for:\n"
        f"Client: {invoice['client_name'] or 'the client'}\n"
        f"Invoice #: {invoice['invoice_number']}\n"
        f"Amount due: ${invoice['amount']:,.2f}\n"
        f"Due date: {invoice['due_date']}\n"
        f"Days overdue: {invoice['days_overdue']}\n"
        f"Project: {invoice['project_name'] or '(unspecified)'}"
    )
    return system, user


def _is_draft_usable(draft, invoice):
    """Garbage/unavailable-LLM guard -- see module docstring: never hand an
    un-drafted or bogus reminder to the approval flow."""
    if not draft or not draft.strip():
        return False
    if draft.startswith("(LLM unavailable"):
        return False
    if len(draft.strip()) < MIN_DRAFT_CHARS:
        return False
    invoice_number = invoice.get("invoice_number") or ""
    if invoice_number and invoice_number not in draft:
        return False
    return True


def _draft_reminder(invoice):
    """Draft via local Ollama. Returns the draft text, or None if the LLM is
    unavailable or the output is unusable -- callers must treat None as
    "skip this invoice, do not suggest anything"."""
    system, user = _build_draft_prompt(invoice)
    try:
        draft = ollama_generate(MODEL, system, user, max_tokens=400)
    except Exception as e:
        log.warning(f"invoice_followup: ollama_generate raised for invoice "
                    f"{invoice['invoice_number']!r}: {e}")
        return None
    if not _is_draft_usable(draft, invoice):
        log.warning(f"invoice_followup: unusable/garbage draft for invoice "
                    f"{invoice['invoice_number']!r}, skipping (never sending an un-drafted reminder)")
        return None
    return draft.strip()


# ── Delivery: reuse skills/shared/suggest_action.py's approval mechanism ───

def _build_suggestion_args(invoice, draft):
    """Build the suggest_action.py SKILL_ARGS payload for one drafted
    reminder. `auto_execute` is deliberately left empty -- even an
    "approved" reply only logs approval in task_journal; nothing executes
    automatically. Serge reviews the draft in the Telegram card and sends
    it himself. suggest_action.py itself html.escape()s title/reasoning/
    proposed_action before building the Telegram message, so this function
    passes plain, unescaped text (double-escaping would corrupt it)."""
    amount = invoice["amount"]
    days_overdue = invoice["days_overdue"]
    invoice_number = invoice["invoice_number"]
    client = invoice["client_name"] or "client on file"
    title = f"Send overdue-invoice reminder — {invoice_number} (${amount:,.2f}, {days_overdue}d overdue)"
    reasoning = (
        f"Invoice {invoice_number} for {client} is {days_overdue} days past due "
        f"(due {invoice['due_date']}). Amount outstanding: ${amount:,.2f}."
    )
    to = invoice["client_email"] or "(no email on file -- confirm contact before sending)"
    proposed_action = (
        f"To: {to}\n"
        f"Subject: Payment reminder — Invoice {invoice_number}\n\n"
        f"{draft}"
    )
    return {
        "category": "alert",
        "title": title,
        "reasoning": reasoning,
        "proposed_action": proposed_action,
        "auto_execute": "",
    }


def send_suggestion(args, agent_id=AGENT_ID, timeout=SUGGEST_ACTION_TIMEOUT):
    """Deliver a drafted reminder as a Serge approve-to-send card by
    invoking skills/shared/suggest_action.py -- the SAME subprocess shape
    core/skills_engine.py's SkillsEngine.run() uses for every other skill
    (SKILL_ARGS + AGENT_ID env vars, script run as a subprocess). This is
    a literal reuse of the existing approval mechanism rather than a
    reimplementation of Telegram send/wait/log.

    Never raises (mirrors cron_helpers' send_alert/send_report swallow-and-
    log style). Returns True iff the subprocess ran and exited 0 (a card
    was sent and resolved -- approved, denied, or timed out all count);
    False if it couldn't even be dispatched.
    """
    env = os.environ.copy()
    env["SKILL_ARGS"] = json.dumps(args)
    env["AGENT_ID"] = agent_id
    try:
        proc = subprocess.run(
            [sys.executable, SUGGEST_ACTION_SCRIPT],
            capture_output=True, text=True, env=env, timeout=timeout,
        )
        if proc.returncode != 0:
            log.warning(f"invoice_followup: suggest_action exited {proc.returncode}: "
                        f"{(proc.stderr or proc.stdout)[:300]}")
        return proc.returncode == 0
    except Exception as e:
        log.error(f"invoice_followup: suggest_action invocation failed: {e}")
        return False


# ── main ─────────────────────────────────────────────────────────────────

def main(now=None, dry_run=False):
    when = now or datetime.datetime.now()
    # retrofit-exempt: delivery goes through the suggest_action approval skill
    # (subprocess) and dedup via cron_health_db.should_alert directly -- there is
    # deliberately no direct send_report/send_alert Telegram path in this cron.
    with cron_run(CRON_NAME):
        _run(when, dry_run=dry_run)


def _run(when, dry_run=False):
    conn = get_db()
    try:
        today_str = when.date().isoformat()
        rows = _get_overdue_invoices(conn, today_str)
        if not rows:
            log.info("invoice_followup: no overdue unpaid invoices, nothing to do")
            return

        drafted, suggested, deduped, skipped = 0, 0, 0, 0
        for row in rows:
            invoice = _row_to_invoice(row, when)
            key = f"{DEDUP_PREFIX}{invoice['id']}"

            try:
                send_now, _row_id = chdb.should_alert(
                    key, RENOTIFY_HOURS,
                    {"title": f"Invoice {invoice['invoice_number']} overdue reminder"},
                )
            except Exception as e:
                log.warning(f"invoice_followup: should_alert failed for {key!r}: {e}")
                send_now = True  # fail open: a registry hiccup shouldn't silently eat a reminder

            if not send_now:
                log.info(f"invoice_followup: {key} deduped (within {RENOTIFY_HOURS}h window), "
                          f"skipping without drafting")
                deduped += 1
                continue

            draft = _draft_reminder(invoice)
            if not draft:
                skipped += 1
                continue
            drafted += 1

            args = _build_suggestion_args(invoice, draft)

            if dry_run:
                log.info(f"invoice_followup: [DRY RUN] would suggest — {args['title']}\n"
                         f"{args['proposed_action']}")
                continue

            if send_suggestion(args):
                suggested += 1

        log.info(f"invoice_followup: {len(rows)} overdue invoice(s) — "
                 f"{drafted} drafted, {suggested} suggestion(s) sent, "
                 f"{deduped} deduped, {skipped} skipped (no usable draft)")
    finally:
        conn.close()


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
