"""
Baza Empire — Intent Dispatcher (shared by /api/intents HTTP and Telegram intercept)

Takes a parsed intent envelope and either executes it or returns an error
envelope. Pure-Python — no Flask, no HTTP — so the Telegram handler in
base_agent.py can call this directly without a self-loopback request.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("baza.intent_dispatcher")


def dispatch(envelope: dict, extra: dict | None = None) -> dict[str, Any]:
    """Execute the intent in `envelope`. Returns:
        {"envelope": <env>, "result": <action result>, "status": <int>}

    `extra` may carry caller-side context like agent_id, approved, or
    pre-filled args (e.g. from Telegram chat metadata).
    """
    extra = extra or {}
    intent = envelope.get("intent")
    args = {**envelope.get("args", {}), **extra}

    # Always emit the parsed event so chains see the directive
    try:
        from core import task_events as te
        te.emit("intent_parsed",
                agent_id=(extra.get("agent_id") or "user"),
                payload={"intent": intent, "args": envelope.get("args", {}),
                         "raw": envelope.get("raw", "")})
    except Exception:
        pass

    if envelope.get("errors") and intent in ("unknown", None):
        return {"envelope": envelope, "result": None, "status": 400}

    if intent == "help":
        from core.intent_router import help_text
        return {"envelope": envelope, "result": {"help": help_text()}, "status": 200}

    if intent == "create_baza_project":
        if envelope.get("errors"):
            return {"envelope": envelope, "result": None, "status": 400}
        from core import baza_projects as bp
        try:
            proj = bp.create_project(
                name=args.get("name") or "",
                type_=args.get("type") or "web-app",
                description=args.get("description") or "",
                created_by=args.get("agent_id") or "user",
            )
        except (ValueError, FileExistsError) as e:
            return {"envelope": envelope, "result": {"error": str(e)}, "status": 400}
        return {
            "envelope": envelope,
            "result": {"project": proj, "url": f"/projects/{proj['id']}"},
            "status": 201,
        }

    if intent == "test":
        if envelope.get("errors"):
            return {"envelope": envelope, "result": None, "status": 400}
        from core import baza_projects as bp
        try:
            res = bp.run_command(args["project_id"], "test")
        except FileNotFoundError:
            return {"envelope": envelope, "result": {"error": "project not found"}, "status": 404}
        return {"envelope": envelope, "result": res, "status": 200}

    if intent == "deploy":
        if envelope.get("errors"):
            return {"envelope": envelope, "result": None, "status": 400}
        approved = bool(args.get("approved"))
        if not approved:
            try:
                from core import task_events as te
                te.emit("approval_requested", project_id=args["project_id"],
                        agent_id=(args.get("agent_id") or "user"),
                        payload={"action": "deploy", "details": args})
            except Exception:
                pass
            return {
                "envelope": envelope,
                "result": {
                    "approval_required": True,
                    "action": "deploy",
                    "project_id": args["project_id"],
                    "hint": "re-issue with approved=true to proceed",
                },
                "status": 202,
            }
        from core import baza_projects as bp
        res = bp.run_command(args["project_id"], "deploy", approved=True)
        return {"envelope": envelope, "result": res, "status": 200}

    if intent in ("flash", "render", "preview", "debug", "develop", "iterate"):
        followup_map = {
            "flash":   "#4.8 ESP/STM/LoRa runtime",
            "render":  "use the Render tab in /projects/<id>",
            "preview": "use the Preview tab in /projects/<id>",
            "debug":   "#4.x Debug logs view",
            "develop": "#5 Agent project access",
            "iterate": "#5 Agent project access",
        }
        return {
            "envelope": envelope,
            "result": {
                "pending": True,
                "intent": intent,
                "follow_up": followup_map[intent],
                "message": f"{intent} is recognized but its handler is not yet implemented end-to-end ({followup_map[intent]}).",
            },
            "status": 202,
        }

    return {"envelope": envelope, "result": None, "status": 400}


def telegram_format(out: dict) -> str:
    """Render a dispatcher result as a short Telegram-friendly reply."""
    env = out.get("envelope") or {}
    result = out.get("result") or {}
    intent = env.get("intent")
    errs = env.get("errors") or []
    if intent in ("unknown", None) and errs:
        return "I didn't understand that directive.\n• " + "\n• ".join(errs) + "\nTry /help."

    if intent == "help":
        return "Directives:\n" + result.get("help", "")
    if intent == "create_baza_project" and result.get("project"):
        p = result["project"]
        return (f"✓ Created Baza project `{p['id']}`\n"
                f"name: {p['name']}\ntype: {p['type']}\npath: {p['path']}\n"
                f"open: {result.get('url','')}")
    if intent == "test":
        ok = result.get("success")
        out_snip = (result.get("stdout") or result.get("error") or "")[:1500]
        return ("✓ tests passed\n" if ok else "✗ tests failed\n") + f"```\n{out_snip}\n```"
    if intent == "deploy":
        if result.get("approval_required"):
            return (f"⚠ Approval required for deploy on `{result.get('project_id')}`.\n"
                    f"Reply: /deploy {result.get('project_id')} approved=true to confirm.")
        ok = result.get("success")
        return ("✓ deploy ok\n" if ok else "✗ deploy failed\n") + f"```\n{(result.get('stdout') or result.get('error') or '')[:1500]}\n```"
    if result.get("pending"):
        return f"⏳ {result.get('message','')}\nfollow-up: {result.get('follow_up','')}"
    if result.get("error"):
        return f"✗ error: {result['error']}"
    # Default
    import json as _json
    return f"intent={intent}\n```\n{_json.dumps(result, indent=2)[:1800]}\n```"
