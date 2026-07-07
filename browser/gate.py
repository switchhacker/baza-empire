"""Write gate for logged-in (profile) sessions: state-changing actions pause
for Serge's Telegram approval. Enforced server-side — never by prompt.
Silence (300 s) = denied, per the Specter confirm-before-act rule."""
import asyncio
import logging
import os
import re
import secrets
import sys
from pathlib import Path
from urllib.parse import urlparse

try:
    from browser import db
except ImportError:  # pragma: no cover
    import db

# core.telegram_fmt needs the framework root importable
_FW = str(Path(__file__).resolve().parent.parent)
if _FW not in sys.path:
    sys.path.insert(0, _FW)

log = logging.getLogger("phantom_browser.gate")

GATED_RX = re.compile(
    r"\b(submit|send|post|buy|pay|order|delete|remove|confirm|publish|checkout"
    r"|subscribe|apply|book|transfer|purchase|tweet|reply|comment|share|upload"
    r"|save|update|sign)\b",
    re.I,
)


def is_gated_click(el: dict | None) -> bool:
    if not el:
        return True  # can't classify it in a logged-in session → gate it
    if (el.get("type") or "").lower() == "submit":
        return True
    if el.get("in_form") and el.get("tag") in ("button", "input"):
        return True
    return bool(GATED_RX.search(el.get("text") or ""))


def is_gated_press(key: str, active: dict | None) -> bool:
    if key.lower() not in ("enter", "return"):
        return False
    if not active:
        return False
    return bool(active.get("in_form")) and (active.get("form_method") or "") == "post"


def is_gated_goto(url: str | None) -> bool:
    """A profile-session navigation is gated when the destination carries a
    query string (the classic GET-triggered mutation channel — ?action=delete,
    ?unsubscribe=1, one-click "confirm"/"approve" links, etc.) or its url text
    matches the same mutation-verb heuristic (GATED_RX) used for clicks.
    Plain navigations (no query, no verb) stay ungated so ordinary browsing
    in a profile session isn't disrupted."""
    if not url:
        return False
    if urlparse(url).query:
        return True
    return bool(GATED_RX.search(url))


def _send_telegram(msg: str) -> bool:
    token = os.environ.get("TELEGRAM_SIMON_BATELY", "")
    chat_id = os.environ.get("SERGE_CHAT_ID", "8551331144")
    if not token:
        log.warning("no TELEGRAM_SIMON_BATELY token — approval message not sent")
        return False
    try:
        from core.telegram_fmt import post_html
        return bool(post_html(token, chat_id, msg))
    except Exception:
        log.exception("telegram send failed")
        return False


async def request_approval(session_id: str, action: dict, description: str) -> dict:
    token = secrets.token_urlsafe(16)
    # The approval row is the source of truth and must exist before we ever
    # attempt to notify — the send below can fail or stall and that must
    # never erase the fact that an approval is pending.
    aid = db.create_approval(session_id, action, description, token)
    base = os.environ.get("PB_PUBLIC_URL", "http://100.127.118.103:8100")
    approve = f"{base}/approvals/{aid}/decide?tok={token}&d=approve"
    deny = f"{base}/approvals/{aid}/decide?tok={token}&d=deny"
    # _send_telegram does a blocking requests.post (up to 15s). This function
    # runs on the FastAPI event loop, so the call must happen off-loop or it
    # stalls every other in-flight request for as long as Telegram takes.
    await asyncio.to_thread(
        _send_telegram,
        f"🔒 **Phantom Browser write gate**\n{description}\n\n"
        f"✅ Approve: {approve}\n❌ Deny: {deny}\n\n"
        f"_No answer in 5 min = denied._",
    )
    return {
        "success": True, "status": "pending_approval", "approval_id": aid,
        "detail": "state-changing action in a logged-in session — Serge pinged on "
                  "Telegram; poll GET /approvals/{id} or the browse skill's "
                  "approval_status action. 5 min silence = denied.",
    }
