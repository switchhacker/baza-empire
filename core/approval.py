"""
Baza Empire — Centralized Approval Gate (Graft 2)
--------------------------------------------------
Any agent can request approval from Serge before performing a destructive or
high-stakes action. Sends a Telegram prompt from the agent's own bot token,
blocks until Serge replies "yes"/"no" (or times out), then returns a bool.

This is the agent-agnostic version of agents/specter_voss/openclaw/tools/
approval_gate.py — promoted to core/ so every BaseAgent can call it.

Usage from a BaseAgent subclass:
    from core.approval import request_approval
    if request_approval("phil_hass", "Delete invoice INV-00031",
                        details="Client unreachable for 90 days",
                        category="delete"):
        do_the_delete()
    else:
        return "Skipped — not approved."

Or via the BaseAgent helper (preferred):
    if self.request_approval("Delete invoice INV-00031", category="delete"):
        ...

SOC 2 control: CC6.1 (restricted write access). Every approval request and
its outcome is logged via the agent's task_journal so audits can trace who
authorized what and when.
"""
import os
import json
import time
import html
import logging
import urllib.request

logger = logging.getLogger(__name__)

# Default 5 minutes — Serge has time to read and respond from his phone
APPROVAL_TIMEOUT = int(os.environ.get("APPROVAL_TIMEOUT", "300"))

# Categories that auto-approve (set in env, comma-separated)
# e.g. AUTO_APPROVE_CATEGORIES="research,read,artifact_save"
AUTO_APPROVED_CATEGORIES = {
    c.strip() for c in os.environ.get("AUTO_APPROVE_CATEGORIES", "").split(",") if c.strip()
}

SERGE_CHAT_ID = os.environ.get("SERGE_CHAT_ID", "")

# Common reply normalizations
_APPROVAL_WORDS = {"yes", "y", "approved", "approve", "do it", "go", "go ahead",
                   "ok", "okay", "proceed", "sure", "yep", "yeah"}
_DENIAL_WORDS   = {"no", "n", "deny", "denied", "stop", "cancel", "abort", "nope"}


def _bot_token_for(agent_id: str) -> str:
    """Look up the agent's Telegram bot token from env.
    Convention: TELEGRAM_<AGENT_ID_UPPER>, e.g. TELEGRAM_PHIL_HASS."""
    return os.environ.get(f"TELEGRAM_{agent_id.upper()}", "")


def _send_telegram(token: str, chat_id: str, text: str) -> dict:
    if not token or not chat_id:
        return {"ok": False, "error": "missing token or chat_id"}
    from core.telegram_fmt import post_html
    ok = post_html(token, chat_id, text, already_html=True)
    return {"ok": ok} if ok else {"ok": False, "error": "telegram send failed (post_html; see logs)"}


def _get_updates(token: str, offset: int = 0) -> list:
    if not token:
        return []
    url = f"https://api.telegram.org/bot{token}/getUpdates?offset={offset}&timeout=5"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read()).get("result", [])
    except Exception:
        return []


def _wait_for_reply(token: str, expected_chat_id: str, timeout: int) -> str:
    """Poll for a reply from Serge's chat. Returns lowercased text or 'timeout'."""
    start = time.time()
    updates = _get_updates(token)
    offset = (updates[-1]["update_id"] + 1) if updates else 0

    while time.time() - start < timeout:
        updates = _get_updates(token, offset)
        for update in updates:
            offset = update["update_id"] + 1
            msg = update.get("message", {})
            text = msg.get("text", "").strip().lower()
            chat_id = str(msg.get("chat", {}).get("id", ""))
            if chat_id == str(expected_chat_id) and text:
                return text
        time.sleep(3)
    return "timeout"


def request_approval(agent_id: str, action: str, details: str = "",
                     category: str = "general", timeout: int = None) -> bool:
    """
    Request approval from Serge for a destructive/high-stakes action.

    Args:
        agent_id: which agent is asking (e.g. "phil_hass") — used for the bot token
        action:   one-line summary of what's about to happen
        details:  optional multi-line context (params, file paths, dollar amounts)
        category: bucket for auto-approval (e.g. "delete", "send_email", "spend")
        timeout:  override APPROVAL_TIMEOUT (default 5 min)

    Returns: True if approved, False if denied or timed out.
    """
    timeout = timeout or APPROVAL_TIMEOUT

    # ── Auto-approve fast-path ───────────────────────────────────────────────
    if category and category in AUTO_APPROVED_CATEGORIES:
        logger.info(f"[approval] {agent_id}: auto-approved [{category}] {action}")
        _journal(agent_id, action, category, "auto_approved", details)
        return True

    token = _bot_token_for(agent_id)
    if not token or not SERGE_CHAT_ID:
        logger.warning(f"[approval] {agent_id}: no bot token or SERGE_CHAT_ID — denying by default")
        _journal(agent_id, action, category, "no_token", details)
        return False

    pretty_agent = agent_id.replace("_", " ").title()
    msg = (
        f"<b>{html.escape(pretty_agent.upper())} — APPROVAL REQUEST</b>\n"
        f"{'━' * 28}\n\n"
        f"<b>Action:</b> {html.escape(action)}\n"
        f"<b>Category:</b> {html.escape(category)}\n"
    )
    if details:
        msg += f"\n<b>Details:</b>\n<code>{html.escape(details[:800])}</code>\n"
    msg += (
        f"\n{'━' * 28}\n"
        f"Reply <b>yes</b> to approve · <b>no</b> to deny\n"
        f"<i>Auto-denies in {timeout}s</i>"
    )

    result = _send_telegram(token, SERGE_CHAT_ID, msg)
    if not result.get("ok"):
        logger.error(f"[approval] {agent_id}: send failed: {result.get('error')}")
        _journal(agent_id, action, category, "send_failed", details)
        return False

    logger.info(f"[approval] {agent_id}: waiting for Serge ({timeout}s) — {action}")
    reply = _wait_for_reply(token, SERGE_CHAT_ID, timeout)

    if reply in _APPROVAL_WORDS:
        _send_telegram(token, SERGE_CHAT_ID, f"Approved. Executing: {html.escape(action)}")
        logger.info(f"[approval] {agent_id}: APPROVED — {action}")
        _journal(agent_id, action, category, "approved", details)
        return True
    if reply in _DENIAL_WORDS:
        _send_telegram(token, SERGE_CHAT_ID, f"Denied. Skipping: {html.escape(action)}")
        logger.info(f"[approval] {agent_id}: DENIED — {action}")
        _journal(agent_id, action, category, "denied", details)
        return False

    _send_telegram(token, SERGE_CHAT_ID, f"Timed out. Skipping: {html.escape(action)}")
    logger.info(f"[approval] {agent_id}: TIMEOUT — {action}")
    _journal(agent_id, action, category, "timeout", details)
    return False


def _journal(agent_id: str, action: str, category: str, outcome: str, details: str):
    """Best-effort task_journal write so the audit trail captures every gate event."""
    try:
        from core.context_db import journal_log
        journal_log(
            agent_id=agent_id,
            task_type="approval_gate",
            task_description=f"[{category}] {action}",
            result=outcome,
            success=(outcome in ("approved", "auto_approved")),
            input_data={"category": category, "details": details[:500]},
        )
    except Exception as e:
        logger.warning(f"[approval] journal write failed: {e}")
