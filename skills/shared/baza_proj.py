#!/usr/bin/env python3
"""
Baza Empire — Baza Projects skill (sub-project #5)

Lets agents create, develop, test, and deploy inside Baza Projects via the
dashboard's /api/baza/projects HTTP API. Mirrors what the user can do in the
/projects tab of the dashboard.

SKILL_ARGS:
  action: one of the typed actions in ACTIONS, OR "raw" for arbitrary calls
  args:   action-specific kwargs

Examples:
  ##SKILL:baza_proj{"action":"list"}##
  ##SKILL:baza_proj{"action":"create","args":{"name":"Lead Capture App","type":"web-app","description":"Form -> Postgres -> Telegram alert"}}##
  ##SKILL:baza_proj{"action":"file_write","args":{"id":"lead-capture-app-abc123","path":"src/app.py","content":"print('hi')"}}##
  ##SKILL:baza_proj{"action":"run","args":{"id":"lead-capture-app-abc123","slot":"test"}}##
  ##SKILL:baza_proj{"action":"run","args":{"id":"lead-capture-app-abc123","slot":"deploy","approved":true}}##

Privileged actions (delete, deploy, flash) refuse to run without
args.approved=true and emit approval_requested events.
"""
import json
import os
import sys
import urllib.error
import urllib.request

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, FRAMEWORK_DIR)

DASHBOARD_URL = os.environ.get("BAZA_DASHBOARD_URL", "http://localhost:8888")
TIMEOUT = int(os.environ.get("BAZA_PROJ_API_TIMEOUT", "120"))

try:
    from core import task_events as _te  # type: ignore
except Exception:
    _te = None


# Action → (HTTP method, path-template, required-args, privileged)
ACTIONS: dict[str, tuple[str, str, list[str], bool]] = {
    "list":        ("GET",    "/api/baza/projects",                  [], False),
    "get":         ("GET",    "/api/baza/projects/{id}",             ["id"], False),
    "create":      ("POST",   "/api/baza/projects",                  ["name"], False),
    "update":      ("PUT",    "/api/baza/projects/{id}",             ["id"], False),
    "delete":      ("DELETE", "/api/baza/projects/{id}",             ["id"], True),
    "files":       ("GET",    "/api/baza/projects/{id}/files",       ["id"], False),
    "file_read":   ("GET",    "/api/baza/projects/{id}/file",        ["id", "path"], False),
    "file_write":  ("POST",   "/api/baza/projects/{id}/file",        ["id", "path", "content"], False),
    "run":         ("POST",   "/api/baza/projects/{id}/run",         ["id", "slot"], False),
}


def _emit(kind: str, payload: dict, parent_event_id=None):
    if _te is None:
        return None
    try:
        return _te.emit(
            kind,
            agent_id=os.environ.get("AGENT_ID", "skill"),
            project_id=payload.get("project_id"),
            payload=payload,
            parent_event_id=parent_event_id,
        )
    except Exception:
        return None


def _http(method: str, path: str, body: dict | None = None, query: dict | None = None) -> dict:
    url = DASHBOARD_URL.rstrip("/") + path
    if query:
        from urllib.parse import urlencode
        url += ("&" if "?" in url else "?") + urlencode({k: v for k, v in query.items() if v is not None})
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


def _fmt_path(template: str, kw: dict) -> str:
    out = template
    for k in list(kw.keys()):
        token = "{" + k + "}"
        if token in out:
            out = out.replace(token, str(kw[k]))
    return out


def _path_keys(template: str, kw: dict) -> set[str]:
    return {k for k in kw if "{" + k + "}" in template}


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
        if not path.startswith("/api/baza/"):
            print(json.dumps({"ok": False, "error": "path must start with /api/baza/"}))
            return 1
        parent = _emit("tool_call", {"tool": "baza_proj.raw", "args": {"method": method, "path": path}})
        result = _http(method, path, body)
        _emit(
            "tool_result",
            {
                "tool": "baza_proj.raw", "ok": result.get("ok"),
                "status": result.get("status"),
                "result_snippet": json.dumps(result.get("body"))[:600] if isinstance(result.get("body"), (dict, list)) else str(result.get("body"))[:600],
            },
            parent_event_id=parent,
        )
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

    # Slot=deploy or slot=flash on `run` is also privileged regardless of the
    # action-level flag — gate at the slot layer too.
    slot_privileged = (action == "run" and payload.get("slot") in ("deploy", "flash"))

    if (privileged or slot_privileged) and not bool(payload.get("approved")):
        _emit(
            "approval_requested",
            {
                "action": ("baza_proj." + action +
                           (f".{payload.get('slot')}" if slot_privileged else "")),
                "details": payload,
                "project_id": payload.get("id"),
            },
        )
        print(json.dumps({
            "ok": False, "approval_required": True,
            "action": "baza_proj." + action,
            "hint": "rerun with args.approved=true after the user approves",
        }))
        return 3

    used = _path_keys(path_tpl, payload)
    path = _fmt_path(path_tpl, payload)

    # Special-case: file_read uses query string for `path`
    body: dict | None = None
    query: dict | None = None
    if action == "file_read":
        query = {"path": payload.get("path")}
    elif method.upper() in ("GET", "DELETE"):
        body = None
    else:
        body = {k: v for k, v in payload.items() if k not in used and k != "approved"}

    # Project ownership/locking: when an agent writes a file, pass our
    # AGENT_ID so the dashboard's cooperative lock picks us up. If the
    # project is held by another agent, the dashboard returns 423 and we
    # surface that to the caller — agents can choose to wait or force.
    if action == "file_write" and isinstance(body, dict) and "agent_id" not in body:
        body["agent_id"] = os.environ.get("AGENT_ID", "skill")

    parent = _emit(
        "tool_call",
        {
            "tool": "baza_proj." + action,
            "project_id": payload.get("id"),
            "args": {k: v for k, v in payload.items() if k != "approved"},
        },
    )
    result = _http(method, path, body, query=query)
    _emit(
        "tool_result",
        {
            "tool": "baza_proj." + action,
            "project_id": payload.get("id"),
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
