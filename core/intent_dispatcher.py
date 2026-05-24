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

    if intent in ("develop", "iterate"):
        return _handle_develop_or_iterate(envelope, args, intent)

    if intent == "scaffold_decompose":
        return _handle_scaffold_decompose(envelope, args)

    if intent in ("flash", "render", "preview", "debug"):
        followup_map = {
            "flash":   "use the Deploy tab Flash card on firmware projects",
            "render":  "use the Render tab in /projects/<id>",
            "preview": "use the Preview tab in /projects/<id>",
            "debug":   "#4.x Debug logs view",
        }
        return {
            "envelope": envelope,
            "result": {
                "pending": True,
                "intent": intent,
                "follow_up": followup_map[intent],
                "message": f"{intent} is recognized but better used via UI ({followup_map[intent]}).",
            },
            "status": 202,
        }

    return {"envelope": envelope, "result": None, "status": 400}


# ── /develop and /iterate — create a task, hand to an agent ──────────────────

DEFAULT_DEV_AGENT = "claw_batto"


def _handle_develop_or_iterate(envelope: dict, args: dict, intent: str) -> dict[str, Any]:
    """Create a task in baza_projects.db, structured so any agent can pick it
    up and use the baza_proj skill to do the work."""
    project_id = args.get("project_id")
    goal = (args.get("goal") or "").strip()
    if not project_id:
        return {"envelope": envelope, "result": {"error": "project_id is required"}, "status": 400}
    if not goal:
        return {"envelope": envelope, "result": {"error": "goal is required (after project_id)"}, "status": 400}

    # Verify project exists
    from core import baza_projects as bp
    proj = bp.get_project(project_id)
    if not proj:
        return {"envelope": envelope, "result": {"error": f"project not found: {project_id}"}, "status": 404}

    # Smart routing: caller can pin via args.agent, otherwise we route by
    # keyword on the goal text using Duke's ROUTING map (shared with the
    # roadmap skill so the two surfaces stay consistent). Firmware project
    # types always go to dev — the goal text is irrelevant. For other
    # types we route purely on the goal so "research X" goes to Scout
    # whether the project is a library or a web-app.
    agent_id = args.get("agent")
    if not agent_id:
        try:
            from skills.shared.duke_roadmap import route_for as _route_for
            ptype = (proj.get("type") or "").lower()
            if ptype in ("esp-firmware", "stm-firmware", "lora-test"):
                agent_id = "claw_batto"
            else:
                agent_id = _route_for(goal)
        except Exception:
            agent_id = DEFAULT_DEV_AGENT
    agent_id = agent_id or DEFAULT_DEV_AGENT
    priority = args.get("priority") or "high"

    # Build a task description that bakes in the skill-call pattern. Any agent
    # inheriting from base_agent has the BAZA PROJECTS section in its system
    # prompt; the description gives it the concrete project_id + goal.
    title = (f"Develop: {goal[:80]}" if intent == "develop"
             else f"Iterate: {goal[:80]}")
    files_hint = "  ##SKILL:baza_proj{\"action\":\"files\",\"args\":{\"id\":\"" + project_id + "\"}}##"
    write_hint = ("  ##SKILL:baza_proj{\"action\":\"file_write\",\"args\":"
                  "{\"id\":\"" + project_id + "\",\"path\":\"<rel/path>\",\"content\":\"<content>\"}}##")
    test_hint = ("  ##SKILL:baza_proj{\"action\":\"run\",\"args\":"
                 "{\"id\":\"" + project_id + "\",\"slot\":\"test\"}}##")
    description = (
        f"Project: {proj['id']}  ({proj['type']})\n"
        f"Path:    {proj['path']}\n"
        f"Goal:    {goal}\n\n"
        "Use the baza_proj skill to read/write files and run tests in this "
        "project. The project has its own git repo and sandboxed file tree.\n\n"
        f"List existing files:\n{files_hint}\n\n"
        f"Write code:\n{write_hint}\n\n"
        f"Run tests:\n{test_hint}\n\n"
        "Save any documentation/notes as artifacts via ##SKILL:artifact_save## "
        f"with project_id=\"{project_id}\". When fully done, end your response "
        "with TASK_COMPLETE."
    )

    # Insert the task directly into baza_projects.db (avoid HTTP roundtrip)
    import os as _os
    import sqlite3 as _sqlite3
    import uuid as _uuid
    db_path = _os.environ.get(
        "BAZA_TASK_EVENTS_DB",
        _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                      "dashboard", "baza_projects.db"),
    )
    task_id = str(_uuid.uuid4())[:8]
    now = _now_iso()
    try:
        conn = _sqlite3.connect(db_path, timeout=10)
        conn.execute(
            """
            INSERT INTO tasks
              (id, project_id, title, description, assigned_to, status, priority,
               notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?, '', ?, ?)
            """,
            (task_id, project_id, title, description, agent_id, priority, now, now),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        return {"envelope": envelope, "result": {"error": f"task insert failed: {e}"}, "status": 500}

    # Emit a chain-spine event so /chains has a row to render right away
    try:
        from core import task_events as te
        te.emit("intent_parsed", project_id=project_id, agent_id=agent_id,
                payload={"intent": intent, "task_id": task_id, "goal": goal})
        te.emit("task_started", task_id=task_id, project_id=project_id, agent_id=agent_id,
                payload={"title": title, "queued": True})
    except Exception:
        pass

    # SYNC mode: run the first task_runner iteration inline so the directive
    # bar shows immediate output. Capped via BAZA_DEVELOP_SYNC_TIMEOUT (default
    # 90s — short enough not to hang the dashboard, long enough for a warm
    # model. Cold-loaded models will fall through to queued mode.)
    sync_mode = (
        bool(args.get("sync"))
        or _os.environ.get("BAZA_DEVELOP_SYNC", "0") in ("1", "true", "yes")
    )
    sync_result = None
    if sync_mode:
        sync_result = _run_first_iteration_inline(agent_id, task_id, title, description, project_id)

    # Otherwise (or in addition), kick task_runner in the background if
    # BAZA_AUTO_RUN_DEVELOP=1. Sync mode supersedes since it already ran.
    if not sync_mode and _os.environ.get("BAZA_AUTO_RUN_DEVELOP", "0") in ("1", "true", "yes"):
        try:
            import subprocess as _sp
            framework_dir = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
            venv_py = _os.path.join(framework_dir, "venv", "bin", "python")
            if not _os.path.exists(venv_py):
                venv_py = "python3"
            _sp.Popen(
                [venv_py, _os.path.join(framework_dir, "core", "task_runner.py"),
                 "--agent", agent_id, "--task-id", task_id],
                cwd=framework_dir, start_new_session=True,
                stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
            )
        except Exception:
            pass  # cron tick will pick it up

    return {
        "envelope": envelope,
        "result": {
            "task_id": task_id,
            "agent": agent_id,
            "project_id": project_id,
            "title": title,
            "url": f"/chains?task_id={task_id}",
            "task_url": f"/tasks#{task_id}",
            "auto_run": _os.environ.get("BAZA_AUTO_RUN_DEVELOP", "0") in ("1", "true", "yes"),
            "sync_run": sync_result,
            "hint": (
                "First iteration ran inline (see sync_run.output)."
                if sync_result and sync_result.get("ok")
                else "Task queued. Will run on next baza-task-runner tick "
                     "(or set BAZA_AUTO_RUN_DEVELOP=1 to kick immediately, "
                     "or BAZA_DEVELOP_SYNC=1 for inline first iteration)."
            ),
        },
        "status": 201,
    }


# ── scaffold_decompose — create a task for Claw to decompose a root node ─────

def _handle_scaffold_decompose(envelope: dict, args: dict) -> dict[str, Any]:
    """Insert a task in baza_projects.db assigned to claw_batto, instructing him
    to call web_search + scaffold_emit_nodes + scaffold_complete_node on the
    given root scaffold node. Returns a flat dict (so the scaffold blueprint
    can read .task_id at top level)."""
    import os as _os
    import sqlite3 as _sqlite3
    import uuid as _uuid
    from pathlib import Path as _Path

    project_id = envelope.get("project_id") or args.get("project_id")
    root_node_id = envelope.get("root_node_id") or args.get("root_node_id")
    description = (envelope.get("description") or args.get("description") or "").strip()

    if not project_id or root_node_id is None:
        return {"error": "missing project_id or root_node_id"}, 400

    db = _os.environ.get(
        "BAZA_PROJECTS_DB",
        str(_Path(__file__).resolve().parents[1] / "dashboard" / "baza_projects.db"),
    )

    title = f"[scaffold decompose] {description[:50]}"
    prompt = (
        f"You are decomposing scaffold root node #{root_node_id} for Baza "
        f"project `{project_id}`.\n\n"
        f"Root description: {description}\n\n"
        "Plan the build tree and emit 4-8 first-level child nodes. Steps:\n\n"
        "1. Understand the topic — call the web_search skill for context "
        "on the build. Example:\n"
        f"   ##SKILL:web_search{{\"query\": \"{description[:80]}\", "
        "\"max_results\": 5}}##\n\n"
        "2. Plan the tree. Identify hardware vs software branches. For a "
        "hardware project, include nodes for major sub-systems "
        "(power, sensors, enclosure, firmware), and include AT LEAST ONE "
        "`decision` node where a real architectural choice has to be made.\n\n"
        "3. Emit child nodes under the root via scaffold_emit_nodes. Each "
        "child needs `title`, `type` (one of: root, decision, "
        "hardware_component, firmware, software_module, deliverable), and "
        "optionally `description`. Example:\n"
        f"   ##SKILL:scaffold_emit_nodes{{\"project_id\": \"{project_id}\", "
        f"\"parent_id\": {root_node_id}, \"nodes\": ["
        "{\"title\": \"...\", \"type\": \"hardware_component\", "
        "\"description\": \"...\"}, ...]}}##\n\n"
        "4. Mark the root node done so the scaffold runner can advance:\n"
        f"   ##SKILL:scaffold_complete_node{{\"node_id\": {root_node_id}, "
        "\"result\": \"decomposed\"}}##\n\n"
        "End your response with TASK_COMPLETE when both skills have run "
        "successfully."
    )

    now = _now_iso()
    try:
        conn = _sqlite3.connect(db, timeout=10)
        # Detect schema: production dashboard has id TEXT PK + priority TEXT;
        # the lightweight test fixture has id INTEGER PK AUTOINCREMENT + priority INTEGER.
        cols = {row[1]: (row[2] or "").upper() for row in
                conn.execute("PRAGMA table_info(tasks)").fetchall()}
        id_is_int = "INT" in cols.get("id", "")
        priority_val = 9 if "INT" in cols.get("priority", "") else "high"
        has_updated_at = "updated_at" in cols
        has_notes = "notes" in cols

        if id_is_int:
            # Let autoincrement assign the id; read it back via lastrowid.
            field_list = ["project_id", "title", "description",
                          "assigned_to", "status", "priority"]
            value_list = [project_id, title, prompt,
                          "claw_batto", "pending", priority_val]
            if has_notes:
                field_list.append("notes")
                value_list.append("")
            if "created_at" in cols:
                field_list.append("created_at")
                value_list.append(now)
            if has_updated_at:
                field_list.append("updated_at")
                value_list.append(now)
            placeholders = ",".join("?" * len(field_list))
            cur = conn.execute(
                f"INSERT INTO tasks ({','.join(field_list)}) VALUES ({placeholders})",
                value_list,
            )
            task_id = cur.lastrowid
        else:
            task_id = str(_uuid.uuid4())[:8]
            conn.execute(
                """
                INSERT INTO tasks
                  (id, project_id, title, description, assigned_to, status, priority,
                   notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'claw_batto', 'pending', ?, '', ?, ?)
                """,
                (task_id, project_id, title, prompt, priority_val, now, now),
            )
        conn.commit()
        conn.close()
    except Exception as e:
        return {"error": f"task insert failed: {e}"}, 500

    # Emit chain-spine events so /chains and the scaffold side panel can see
    # the dispatch immediately.
    try:
        from core import task_events as te
        te.emit("intent_parsed", project_id=project_id, agent_id="claw_batto",
                payload={"intent": "scaffold_decompose", "task_id": task_id,
                         "root_node_id": root_node_id})
        te.emit("task_started", task_id=task_id, project_id=project_id,
                agent_id="claw_batto",
                payload={"title": title, "queued": True,
                         "root_node_id": root_node_id})
    except Exception:
        pass

    return {
        "ok": True,
        "task_id": task_id,
        "agent": "claw_batto",
        "project_id": project_id,
        "root_node_id": root_node_id,
    }, 200


def _run_first_iteration_inline(agent_id: str, task_id: str, title: str,
                                  description: str, project_id: str) -> dict[str, Any]:
    """Run a single task_runner iteration inline, return stdout/exit/duration.

    Capped at BAZA_DEVELOP_SYNC_TIMEOUT seconds so the dashboard request
    doesn't hang on a cold model. On timeout/error, returns ok=False — the
    task stays pending and the next cron tick (or user retry) handles it.
    """
    import os as _os
    import subprocess as _sp
    import time as _time
    timeout = int(_os.environ.get("BAZA_DEVELOP_SYNC_TIMEOUT", "90"))
    framework_dir = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    venv_py = _os.path.join(framework_dir, "venv", "bin", "python")
    if not _os.path.exists(venv_py):
        venv_py = "python3"
    cmd = [venv_py, _os.path.join(framework_dir, "core", "task_runner.py"),
           "--agent", agent_id, "--task-id", task_id]
    t0 = _time.time()
    try:
        proc = _sp.run(cmd, cwd=framework_dir, capture_output=True,
                       text=True, timeout=timeout)
        dt = _time.time() - t0
        return {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "duration_s": round(dt, 1),
            "output_tail": (proc.stdout or "")[-1500:],
            "stderr_tail": (proc.stderr or "")[-500:],
        }
    except _sp.TimeoutExpired as e:
        return {
            "ok": False,
            "error": f"sync run timed out after {timeout}s — task stays queued",
            "duration_s": timeout,
            "output_tail": (e.stdout or "")[-800:] if isinstance(e.stdout, str) else "",
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _now_iso() -> str:
    import datetime as _dt
    return _dt.datetime.utcnow().replace(microsecond=0).isoformat()


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
    if intent in ("develop", "iterate") and result.get("task_id"):
        return (f"✓ {intent} task queued: `{result['task_id']}`\n"
                f"agent: {result['agent']}\nproject: {result['project_id']}\n"
                f"watch: {result['url']}")
    if result.get("pending"):
        return f"⏳ {result.get('message','')}\nfollow-up: {result.get('follow_up','')}"
    if result.get("error"):
        return f"✗ error: {result['error']}"
    # Default
    import json as _json
    return f"intent={intent}\n```\n{_json.dumps(result, indent=2)[:1800]}\n```"
