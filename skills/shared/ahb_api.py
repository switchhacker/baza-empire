#!/usr/bin/env python3
"""
Baza Empire — AHB123 HTTP API Skill (sub-project #3)

Lets any agent call the dashboard's AHB123 HTTP layer the same way the
browser UI does. Covers business logic the direct-SQL skill (ahb123_query.py)
doesn't reach: quote PDFs, receipt OCR, voice synthesis, blueprint rendering,
architect image generation, status sync, etc.

SKILL_ARGS:
  action: one of the typed actions in ACTIONS, OR "raw" for arbitrary calls
  args:   action-specific kwargs (or {method, path, body} when action=raw)

Returns JSON on stdout.

Privileged actions (delete, deploy-style) are gated: refuse unless
args.approved == True. The skill emits tool_call / tool_result events
to task_events so the chain in /chains shows what happened.

Examples:
  ##SKILL:ahb_api{"action":"clients_list"}##
  ##SKILL:ahb_api{"action":"projects_create","args":{"title":"Kitchen Reno","budget_low":18000,"budget_high":24000}}##
  ##SKILL:ahb_api{"action":"raw","args":{"method":"GET","path":"/api/ahb/architect/images/foo.png"}}##
"""
import json
import os
import sys
import urllib.error
import urllib.request

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, FRAMEWORK_DIR)

DASHBOARD_URL = os.environ.get("BAZA_DASHBOARD_URL", "http://localhost:8888")
TIMEOUT = int(os.environ.get("BAZA_AHB_API_TIMEOUT", "60"))

try:
    from core import task_events as _te  # type: ignore
except Exception:
    _te = None


# Map action name → (HTTP method, path-template, kwargs-required, privileged)
ACTIONS: dict[str, tuple[str, str, list[str], bool]] = {
    # Clients
    "clients_list":    ("GET",    "/api/ahb/clients",                          [], False),
    "clients_create":  ("POST",   "/api/ahb/clients",                          ["name"], False),
    "clients_update":  ("PUT",    "/api/ahb/clients/{id}",                     ["id"], False),
    "clients_delete":  ("DELETE", "/api/ahb/clients/{id}",                     ["id"], True),
    # Projects
    "projects_list":         ("GET",    "/api/ahb/projects",                   [], False),
    "projects_create":       ("POST",   "/api/ahb/projects",                   ["title"], False),
    "projects_update":       ("PUT",    "/api/ahb/projects/{id}",              ["id"], False),
    "projects_delete":       ("DELETE", "/api/ahb/projects/{id}",              ["id"], True),
    "project_status_sync":   ("POST",   "/api/ahb/projects/{id}/status",       ["id"], False),
    "project_move_year":     ("POST",   "/api/ahb/projects/{id}/move-to-year", ["id", "year"], False),
    # Quotes
    "quotes_list":     ("GET",    "/api/ahb/projects/{project_id}/quotes",     ["project_id"], False),
    "quotes_create":   ("POST",   "/api/ahb/projects/{project_id}/quotes",     ["project_id"], False),
    "quote_get":       ("GET",    "/api/ahb/quotes/{id}",                      ["id"], False),
    "quote_update":    ("PUT",    "/api/ahb/quotes/{id}",                      ["id"], False),
    "quote_delete":    ("DELETE", "/api/ahb/quotes/{id}",                      ["id"], True),
    # Invoices
    "invoices_list":   ("GET",    "/api/ahb/invoices",                         [], False),
    "invoices_create": ("POST",   "/api/ahb/invoices",                         ["client_id"], False),
    "invoices_update": ("PUT",    "/api/ahb/invoices/{id}",                    ["id"], False),
    "invoices_delete": ("DELETE", "/api/ahb/invoices/{id}",                    ["id"], True),
    "invoice_move_year": ("POST", "/api/ahb/invoices/{id}/move-to-year",       ["id", "year"], False),
    # Receipts
    "receipts_list":   ("GET",    "/api/ahb/receipts",                         [], False),
    "receipts_create": ("POST",   "/api/ahb/receipts",                         [], False),
    "receipt_get":     ("GET",    "/api/ahb/receipts/{id}",                    ["id"], False),
    "receipt_update":  ("PUT",    "/api/ahb/receipts/{id}",                    ["id"], False),
    "receipt_ocr":     ("POST",   "/api/ahb/receipts/{id}/ocr",                ["id"], False),
    "receipts_corrections": ("GET", "/api/ahb/receipts/corrections",           [], False),
    # Payroll
    "payroll_list":    ("GET",    "/api/ahb/payroll",                          [], False),
    "payroll_create":  ("POST",   "/api/ahb/payroll",                          [], False),
    "payroll_update":  ("PUT",    "/api/ahb/payroll/{id}",                     ["id"], False),
    # Employees
    "employees_list":   ("GET",    "/api/ahb/employees",                       [], False),
    "employees_create": ("POST",   "/api/ahb/employees",                       ["name"], False),
    "employees_update": ("PUT",    "/api/ahb/employees/{id}",                  ["id"], False),
    "employees_delete": ("DELETE", "/api/ahb/employees/{id}",                  ["id"], True),
    # Events / calendar
    "events_list":   ("GET",  "/api/ahb/events", [], False),
    "events_create": ("POST", "/api/ahb/events", [], False),
    # Estimates
    "estimates_list":     ("GET",  "/api/ahb/estimates",          [], False),
    "estimates_create":   ("POST", "/api/ahb/estimates",          [], False),
    "estimates_generate": ("POST", "/api/ahb/estimates/generate", [], False),
    # Voice
    "voice_voices":         ("GET",    "/api/ahb/voice/voices",          [], False),
    "voice_configs_list":   ("GET",    "/api/ahb/voice/configs",         [], False),
    "voice_configs_create": ("POST",   "/api/ahb/voice/configs",         [], False),
    "voice_configs_delete": ("DELETE", "/api/ahb/voice/configs/{id}",    ["id"], False),
    "voice_synthesize":     ("POST",   "/api/ahb/voice/synthesize",      [], False),
    "voice_logs":           ("GET",    "/api/ahb/voice/logs",            [], False),
    "voice_stats":          ("GET",    "/api/ahb/voice/stats",           [], False),
    # Blueprints
    "blueprints_list":           ("GET",    "/api/ahb/blueprints",                       [], False),
    "blueprints_get":            ("GET",    "/api/ahb/blueprints/{id}",                  ["id"], False),
    "blueprints_create":         ("POST",   "/api/ahb/blueprints",                       [], False),
    "blueprints_update":         ("PUT",    "/api/ahb/blueprints/{id}",                  ["id"], False),
    "blueprints_delete":         ("DELETE", "/api/ahb/blueprints/{id}",                  ["id"], True),
    "blueprints_render":         ("POST",   "/api/ahb/blueprints/{id}/render",           ["id"], False),
    "blueprints_from_description": ("POST", "/api/ahb/blueprints/from-description",      [], False),
    "blueprints_from_photo":     ("POST",   "/api/ahb/blueprints/from-photo",            [], False),
    # Architect
    "architect_analyze":  ("POST", "/api/ahb/architect/analyze",  [], False),
    "architect_generate": ("POST", "/api/ahb/architect/generate", [], False),
    "architect_img2img":  ("POST", "/api/ahb/architect/img2img",  [], False),
    "architect_transform": ("POST", "/api/ahb/architect/transform", [], False),
    # Chats
    "chats_list":           ("GET",  "/api/ahb/chats",                       [], False),
    "chat_messages":        ("GET",  "/api/ahb/chats/{chat_id}/messages",    ["chat_id"], False),
    "chat_message_create":  ("POST", "/api/ahb/chats/{chat_id}/messages",    ["chat_id"], False),
    "chat_history":         ("GET",  "/api/ahb/chats/history",               [], False),
    "chat_stats":           ("GET",  "/api/ahb/chats/stats",                 [], False),
    "chat_export":          ("GET",  "/api/ahb/chats/{chat_id}/export",      ["chat_id"], False),
    "chat_update":          ("PUT",  "/api/ahb/chats/{chat_id}",             ["chat_id"], False),
    "chat_escalate":        ("POST", "/api/ahb/chats/{chat_id}/escalate",    ["chat_id"], False),
    # Activity / search
    "activity_feed":  ("GET", "/api/ahb/activity-feed", [], False),
}


def _emit(kind: str, payload: dict, parent_event_id=None):
    if _te is None:
        return None
    try:
        return _te.emit(
            kind,
            agent_id=os.environ.get("AGENT_ID", "skill"),
            payload=payload,
            parent_event_id=parent_event_id,
        )
    except Exception:
        return None


def _http(method: str, path: str, body: dict | None = None) -> dict:
    url = DASHBOARD_URL.rstrip("/") + path
    data = None
    headers = {"Accept": "application/json"}
    if body is not None and method.upper() not in ("GET", "DELETE"):
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method.upper(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return {"ok": True, "status": resp.status, "body": json.loads(raw) if raw else None}
            except json.JSONDecodeError:
                return {"ok": True, "status": resp.status, "body": raw}
    except urllib.error.HTTPError as e:
        try:
            err_body = json.loads(e.read().decode("utf-8", errors="replace") or "null")
        except Exception:
            err_body = None
        return {"ok": False, "status": e.code, "error": str(e), "body": err_body}
    except Exception as e:
        return {"ok": False, "status": 0, "error": str(e)}


def _format_path(template: str, kw: dict) -> str:
    """Substitute {placeholders} from kw, leave kw items used as path params out of body."""
    out = template
    for k in list(kw.keys()):
        token = "{" + k + "}"
        if token in out:
            out = out.replace(token, str(kw[k]))
    return out


def _path_args_used(template: str, kw: dict) -> set[str]:
    used = set()
    for k in kw:
        if "{" + k + "}" in template:
            used.add(k)
    return used


def main() -> int:
    raw = os.environ.get("SKILL_ARGS", "{}")
    try:
        args = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"ok": False, "error": f"bad JSON: {e}"}))
        return 1

    action = (args.get("action") or "").strip()
    payload = args.get("args") or {}
    if not action:
        print(json.dumps({"ok": False, "error": "action is required (use action='help' to list)"}))
        return 1

    if action in ("help", "list_actions"):
        print(json.dumps({
            "ok": True,
            "actions": sorted(ACTIONS.keys()) + ["raw", "help"],
            "url": DASHBOARD_URL,
        }, indent=2))
        return 0

    if action == "raw":
        method = (payload.get("method") or "GET").upper()
        path = payload.get("path") or ""
        body = payload.get("body")
        if not path.startswith("/api/"):
            print(json.dumps({"ok": False, "error": "path must start with /api/"}))
            return 1
        parent = _emit("tool_call", {"tool": "ahb_api.raw", "args": {"method": method, "path": path}})
        result = _http(method, path, body)
        _emit("tool_result", {"tool": "ahb_api.raw", "ok": result.get("ok"), "status": result.get("status"),
                              "result_snippet": json.dumps(result.get("body"))[:600] if isinstance(result.get("body"), (dict, list)) else str(result.get("body"))[:600]},
              parent_event_id=parent)
        print(json.dumps(result, indent=2, default=str))
        return 0 if result.get("ok") else 2

    if action not in ACTIONS:
        print(json.dumps({"ok": False, "error": f"unknown action: {action}",
                          "hint": "use action='help' to list"}))
        return 1

    method, path_tpl, required, privileged = ACTIONS[action]

    missing = [k for k in required if k not in payload]
    if missing:
        print(json.dumps({"ok": False, "error": f"missing required args: {missing}"}))
        return 1

    if privileged and not bool(payload.get("approved")):
        _emit("approval_requested", {"action": "ahb." + action, "details": payload})
        print(json.dumps({
            "ok": False, "approval_required": True,
            "action": "ahb." + action,
            "hint": "rerun the skill call with args.approved=true after the user approves",
        }))
        return 3

    used_keys = _path_args_used(path_tpl, payload)
    path = _format_path(path_tpl, payload)
    body = {k: v for k, v in payload.items() if k not in used_keys and k != "approved"}
    if method.upper() in ("GET", "DELETE"):
        body = None  # GET/DELETE in this skill — query strings aren't supported in iter1

    parent = _emit("tool_call", {"tool": "ahb." + action, "args": {k: v for k, v in payload.items() if k != "approved"}})
    result = _http(method, path, body)
    _emit(
        "tool_result",
        {
            "tool": "ahb." + action,
            "ok": result.get("ok"),
            "status": result.get("status"),
            "result_snippet": (json.dumps(result.get("body"))[:600] if isinstance(result.get("body"), (dict, list)) else str(result.get("body"))[:600]),
        },
        parent_event_id=parent,
    )
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
