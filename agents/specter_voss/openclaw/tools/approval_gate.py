#!/usr/bin/env python3
"""
Approval Gate — Sends upgrade proposals to Serge via Telegram.
Waits for approval before returning. Blocks execution until approved or denied.

Usage as a module:
    from tools.approval_gate import request_approval
    approved = request_approval("Deploy new weather skill", details="...")

Usage as skill:
    SKILL_ARGS={"action":"Deploy new skill","details":"..."} python3 approval_gate.py
"""
import os
import sys
import json
import time
import socket
import urllib.request
import urllib.parse

# Force IPv6
_orig = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _orig(*a, **k) if r[0] == socket.AF_INET6] or _orig(*a, **k)

BOT_TOKEN = os.environ.get("TELEGRAM_SPECTER_VOSS", "")
SERGE_CHAT_ID = os.environ.get("SERGE_CHAT_ID", "")
APPROVAL_TIMEOUT = int(os.environ.get("APPROVAL_TIMEOUT", "300"))  # 5 min default

# Auto-approved categories (Serge can expand this list)
AUTO_APPROVED = set(os.environ.get("AUTO_APPROVE_CATEGORIES", "").split(","))


def send_telegram(text: str, chat_id: str = None) -> dict:
    """Send a message via Telegram Bot API."""
    cid = chat_id or SERGE_CHAT_ID
    if not BOT_TOKEN or not cid:
        return {"ok": False, "error": "Missing bot token or chat ID"}

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = json.dumps({
        "chat_id": cid,
        "text": text,
        "parse_mode": "HTML",
    }).encode()

    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_updates(offset: int = 0) -> list:
    """Get recent messages sent TO the bot."""
    if not BOT_TOKEN:
        return []
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={offset}&timeout=5"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return data.get("result", [])
    except Exception:
        return []


def wait_for_reply(after_message_id: int, timeout: int = APPROVAL_TIMEOUT) -> str:
    """Poll for Serge's reply after sending an approval request."""
    start = time.time()
    # Get current update offset
    updates = get_updates()
    offset = (updates[-1]["update_id"] + 1) if updates else 0

    while time.time() - start < timeout:
        updates = get_updates(offset)
        for update in updates:
            offset = update["update_id"] + 1
            msg = update.get("message", {})
            text = msg.get("text", "").strip().lower()
            chat_id = str(msg.get("chat", {}).get("id", ""))

            # Only accept replies from Serge's chat
            if chat_id == str(SERGE_CHAT_ID) and text:
                return text

        time.sleep(3)

    return "timeout"


def request_approval(action: str, details: str = "", category: str = "general") -> bool:
    """
    Request approval from Serge for a stealth upgrade.
    Returns True if approved, False if denied or timed out.
    """
    # Check auto-approve
    if category in AUTO_APPROVED and category:
        send_telegram(
            f"<b>AUTO-APPROVED</b> [{category}]\n\n"
            f"<b>Action:</b> {action}\n"
            f"{details[:500] if details else ''}\n\n"
            f"<i>Executing now (auto-approved category)</i>"
        )
        return True

    # Send approval request
    msg = (
        f"<b>SPECTER UPGRADE REQUEST</b>\n"
        f"{'━' * 28}\n\n"
        f"<b>Action:</b> {action}\n"
    )
    if details:
        msg += f"\n<b>Details:</b>\n<code>{details[:800]}</code>\n"
    msg += (
        f"\n{'━' * 28}\n"
        f"Reply <b>yes</b> to approve or <b>no</b> to deny\n"
        f"<i>Auto-denies in {APPROVAL_TIMEOUT}s</i>"
    )

    result = send_telegram(msg)
    if not result.get("ok"):
        print(f"[APPROVAL] Failed to send request: {result.get('error')}")
        return False

    sent_id = result.get("result", {}).get("message_id", 0)
    print(f"[APPROVAL] Waiting for Serge's response (timeout: {APPROVAL_TIMEOUT}s)...")

    reply = wait_for_reply(sent_id, APPROVAL_TIMEOUT)

    approved = reply in ("yes", "y", "approved", "approve", "do it", "go", "go ahead", "ok", "proceed")
    denied = reply in ("no", "n", "deny", "denied", "stop", "cancel", "abort")

    if approved:
        send_telegram(f"Approved. Executing: {action}")
        print(f"[APPROVAL] APPROVED by Serge")
        return True
    elif denied:
        send_telegram(f"Denied. Skipping: {action}")
        print(f"[APPROVAL] DENIED by Serge")
        return False
    else:
        send_telegram(f"Timed out waiting for approval. Skipping: {action}")
        print(f"[APPROVAL] TIMED OUT — treating as denied")
        return False


# ── CLI / Skill entry point ───────────────────────────────────────────────────
if __name__ == "__main__":
    args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
    action = args.get("action", "Unknown upgrade")
    details = args.get("details", "")
    category = args.get("category", "general")

    if request_approval(action, details, category):
        print("APPROVED")
    else:
        print("DENIED")
