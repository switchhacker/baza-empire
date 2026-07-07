#!/usr/bin/env python3
"""Interactive web browsing via the Phantom Browser service (:8100).
Multistep: goto creates a session, read returns the page as markdown plus
NUMBERED interactive elements, then click/type by element index."""
SKILL_META = {
    "category": "web",
    "summary": "Interactive browser session: goto/read/click/type/press/scroll/back/screenshot/close.",
    "when_to_use": ("When a task needs real browsing — JS-heavy pages, walking search "
                    "results, pagination, forms, logged-in sites. Start with "
                    "{\"action\":\"goto\",\"url\":...}; then {\"action\":\"read\"} to see the page "
                    "and its numbered elements; then act by index. Always pass back session_id."),
    "args": {
        "action": "goto|read|click|type|press|scroll|back|screenshot|close|approval_status",
        "session_id": "returned by the first goto — pass it on every later call",
        "url": "for goto", "index": "element index from read (for click/type)",
        "text": "for type", "key": "for press (default Enter)", "dy": "scroll pixels",
        "profile": "optional logged-in profile name (write actions need Serge's approval)",
        "approval_id": "for approval_status",
        "max_chars": "read: markdown size cap (default 6000)",
    },
}
import json
import os
import sys

try:
    args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
except json.JSONDecodeError as e:
    print(json.dumps({"success": False, "error": f"invalid SKILL_ARGS JSON: {e}"}))
    sys.exit(1)

import httpx

BASE = os.environ.get("PHANTOM_BROWSER_URL", "http://localhost:8100")
action = args.get("action", "")
sid = args.get("session_id", "")


def _int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _bool(v):
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def call(method, path, payload=None):
    r = httpx.request(method, f"{BASE}{path}", json=payload, timeout=60)
    r.raise_for_status()
    return r.json()


try:
    if action == "goto" and not sid:
        sid = call("POST", "/session", {"profile": args.get("profile")})["session_id"]

    if action == "close":
        out = call("DELETE", f"/session/{sid}")
    elif action == "approval_status":
        out = call("GET", f"/approvals/{_int(args.get('approval_id'), 0)}")
    elif action in ("goto", "read", "click", "type", "press", "scroll", "back",
                    "screenshot"):
        payload = {k: args.get(k) for k in ("url", "index", "text", "key", "dy")
                   if args.get(k) is not None}
        if action == "read":
            payload["max_chars"] = _int(args.get("max_chars"), 6000)
        out = call("POST", f"/session/{sid}/{action}", payload)
    else:
        out = {"success": False,
               "error": f"unknown action '{action}'",
               "hint": "actions: goto/read/click/type/press/scroll/back/screenshot/close/approval_status"}
    out["session_id"] = sid
    print(json.dumps(out))
except httpx.HTTPError as e:
    print(json.dumps({"success": False, "session_id": sid,
                      "error": f"{type(e).__name__}: {e}",
                      "hint": "is baza-phantom-browser.service running on :8100?"}))
