#!/usr/bin/env python3
"""
Duke Harmon — 4-hourly Task Manager + Active Dispatch Engine

Phase 1: Status snapshot
Phase 2: Direct-dispatch every uncompleted task to its assigned agent's Telegram bot
         with a clear brief. Track dispatch_count.
Phase 3: Escalation ladder — agents that ignore 2+ dispatches get reassigned.
         Tasks that 2+ agents have failed get bumped to Simon for human-grade handling.
Phase 4: Stale follow-up notes (kept from old version)
Phase 5: Roadmap auto-creation when pipeline thins out
Phase 6: Idle-agent assignment
Phase 7: LLM-narrated status report to Serge
"""
import os, sys, json, logging, uuid, datetime, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../.."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))
from agents.cron_helpers import *

logging.basicConfig(level=logging.INFO, format="%(asctime)s [DUKE-TRACKER] %(message)s")

MODEL = "qwen2.5:14b"
AGENT_TOKEN = os.getenv("TELEGRAM_DUKE_HARMON", TELEGRAM_TOKEN)
SERGE_CHAT  = os.getenv("SERGE_CHAT_ID", "8551331144")

AGENT_SPECIALTIES = {
    "simon_bately":  "business strategy, team coordination, client relations",
    "claw_batto":    "engineering, infrastructure, code, DevOps, server management",
    "phil_hass":     "finance, legal, compliance, contracts, tax, accounting",
    "sam_axe":       "design, marketing, branding, images, creative, website content",
    "duke_harmon":   "project management, deadlines, task coordination",
    "rex_valor":     "sales, leads, voicemail, client intake, follow-ups",
    "scout_reeves":  "research, market intelligence, competitor analysis, tech scouting",
    "nova_sterling": "client communication, chat, customer satisfaction, support",
}

# Direct Telegram tokens for each agent — Duke uses these to dispatch task briefs
# straight into the assigned agent's bot conversation with Serge
AGENT_TOKENS = {
    "simon_bately":  os.getenv("TELEGRAM_SIMON_BATELY"),
    "claw_batto":    os.getenv("TELEGRAM_CLAW_BATTO"),
    "phil_hass":     os.getenv("TELEGRAM_PHIL_HASS"),
    "sam_axe":       os.getenv("TELEGRAM_SAM_AXE"),
    "rex_valor":     os.getenv("TELEGRAM_REX_VALOR"),
    "scout_reeves":  os.getenv("TELEGRAM_SCOUT_REEVES"),
    "nova_sterling": os.getenv("TELEGRAM_NOVA_STERLING"),
}

# Best-fit reassignment chain — if agent X is failing on a task, try the next match
REASSIGNMENT_CHAIN = {
    "claw_batto":    ["simon_bately", "scout_reeves"],
    "phil_hass":     ["simon_bately"],
    "sam_axe":       ["scout_reeves", "simon_bately"],
    "rex_valor":     ["nova_sterling", "simon_bately"],
    "scout_reeves":  ["claw_batto", "simon_bately"],
    "nova_sterling": ["rex_valor", "simon_bately"],
    "simon_bately":  [],   # buck stops here
}

DISPATCH_COOLDOWN_HOURS  = 5    # cron runs every 4h; keep cooldown strictly larger to avoid re-firing same tasks each cycle
ESCALATION_DISPATCH_LIMIT = 2    # after 2 dispatches with no progress, reassign
HUMAN_ESCALATE_REASSIGNS  = 2    # after 2 reassignments, escalate to Serge directly


def _send_to_agent(agent_id: str, message: str) -> bool:
    """Record the dispatch in the task journal and Redis event bus so the agent
    picks it up via the task_runner — NO Telegram message to Serge's chat.
    Duke dispatches silently; Serge only sees the summary at the end."""
    try:
        # 1. Log to PostgreSQL task_journal so the agent and team_pulse can see it
        import psycopg2
        conn = psycopg2.connect(
            host=os.environ.get("BAZA_DB_HOST", "localhost"),
            port=5432,
            dbname=os.environ.get("BAZA_DB_NAME", "baza_agents"),
            user=os.environ.get("BAZA_DB_USER", "switchhacker"),
            password=os.environ.get("DB_PASSWORD", "baza2026"),
        )
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO task_journal (agent_id, task_type, task_description, result, success, created_at)
               VALUES (%s, %s, %s, %s, %s, NOW())""",
            (agent_id, "dispatch_received", message[:500], "pending", True)
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        log.warning(f"Journal log for dispatch to {agent_id} failed: {e}")

    try:
        # 2. Publish Redis event so the agent's event listener picks it up
        from core.event_bus import publish_sync
        publish_sync("duke_harmon", "dispatch", {
            "target": agent_id,
            "brief": message[:500],
        })
    except Exception as e:
        log.warning(f"Redis event for dispatch to {agent_id} failed: {e}")

    log.info(f"Dispatched silently to {agent_id} (journal + event bus)")
    return True


def _format_dispatch_brief(task_row, dispatch_num: int) -> str:
    """Build the message Duke sends into the agent's bot."""
    tid       = task_row["id"]
    title     = task_row["title"]
    desc      = (task_row["description"] or "").strip()
    priority  = task_row["priority"] or "medium"
    notes     = (task_row["notes"] or "").strip()
    due       = task_row["due_date"] or "no deadline"
    agent     = task_row["assigned_to"]
    nudge     = ""
    if dispatch_num == 1:
        nudge = "Please pick this up and start working. Reply with progress or mark blocked if you can't."
    elif dispatch_num == 2:
        nudge = "⚠️ SECOND DISPATCH — this task has been pinged before. If you cannot complete it, reply with EXACT blockers so I can reassign."
    else:
        nudge = "⛔ FINAL DISPATCH — escalating to Simon next cycle. Tell me NOW why this is stuck."
    return (
        f"🎯 DUKE DISPATCH #{dispatch_num} — Task assignment\n\n"
        f"📌 {title}\n"
        f"Priority: {priority.upper()} · Due: {due}\n\n"
        f"Description:\n{desc[:600] or '(none)'}\n\n"
        f"{nudge}\n\n"
        f"Task ID: {tid}\n"
        f"Reply: TASK_COMPLETE / TASK_BLOCKED:<reason> / TASK_IN_PROGRESS:<update>"
    )


def _record_dispatch(conn, task_id: str, agent_id: str, dispatch_num: int):
    """Update dispatch_count, last_dispatched_at, and append to dispatch_history."""
    now_iso = datetime.datetime.now().isoformat()
    row = conn.execute("SELECT dispatch_history FROM tasks WHERE id=?", (task_id,)).fetchone()
    history = []
    if row and row[0]:
        try: history = json.loads(row[0])
        except Exception: history = []
    history.append({"at": now_iso, "to": agent_id, "n": dispatch_num})
    conn.execute(
        "UPDATE tasks SET dispatch_count=?, last_dispatched_at=?, dispatch_history=?, updated_at=? WHERE id=?",
        (dispatch_num, now_iso, json.dumps(history), now_iso, task_id)
    )

def collect_status():
    conn = get_db()
    conn.row_factory = __import__('sqlite3').Row
    task_stats = dict(conn.execute("SELECT status, COUNT(*) FROM tasks GROUP BY status").fetchall())
    proj_stats = dict(conn.execute("SELECT status, COUNT(*) FROM ahb_projects GROUP BY status").fetchall())

    # Stale tasks — in_progress for more than 48 hours
    cutoff = (datetime.datetime.now() - datetime.timedelta(hours=48)).isoformat()
    stale = conn.execute(
        "SELECT id, title, assigned_to, updated_at FROM tasks WHERE status='in_progress' AND updated_at < ? ORDER BY updated_at",
        (cutoff,)).fetchall()

    # Blocked tasks
    blocked = conn.execute("SELECT id, title, assigned_to, notes FROM tasks WHERE status='blocked'").fetchall()

    # In progress
    in_prog = conn.execute("SELECT id, title, assigned_to, updated_at FROM tasks WHERE status='in_progress' ORDER BY updated_at DESC LIMIT 10").fetchall()

    # Pending
    pending = conn.execute("SELECT id, title, assigned_to, priority FROM tasks WHERE status='pending' ORDER BY priority DESC, created_at").fetchall()

    # Per-agent workload
    workload = dict(conn.execute(
        "SELECT assigned_to, COUNT(*) FROM tasks WHERE status IN ('pending','in_progress') GROUP BY assigned_to").fetchall())

    conn.close()
    return task_stats, proj_stats, stale, blocked, in_prog, pending, workload


def dispatch_all_uncompleted():
    """THE CORE NEW LOOP: walk every uncompleted task and dispatch it directly to
    its assigned agent's Telegram bot. Tracks dispatch_count, escalates after 2
    no-progress dispatches, and reassigns to the next-best agent. After 2
    reassignments, escalates to Serge directly."""
    import sqlite3 as _sql
    conn = get_db()
    conn.row_factory = _sql.Row

    # Pull every uncompleted, non-archived task
    rows = conn.execute(
        "SELECT * FROM tasks WHERE status NOT IN ('completed','cancelled','closed','done','archived') ORDER BY priority DESC, created_at"
    ).fetchall()

    actions = []
    now = datetime.datetime.now()
    cooldown = datetime.timedelta(hours=DISPATCH_COOLDOWN_HOURS)

    for row in rows:
        tid       = row["id"]
        agent     = row["assigned_to"]
        title     = row["title"] or "(untitled)"
        last_disp = row["last_dispatched_at"]
        disp_n    = (row["dispatch_count"] or 0)
        reass_n   = (row["reassignment_count"] or 0)

        if not agent:
            actions.append(f"⚠ unassigned task: {title[:50]} — needs an owner")
            continue

        # Cooldown — don't spam the same agent every cron cycle
        if last_disp:
            try:
                last_dt = datetime.datetime.fromisoformat(last_disp)
                if (now - last_dt) < cooldown:
                    continue   # too soon to dispatch again
            except Exception:
                pass

        # Check for progress since last dispatch — if updated_at is newer than
        # last_dispatched_at it means the agent (or someone) actually touched
        # the task. Reset dispatch counter to give them another fresh start.
        try:
            up = datetime.datetime.fromisoformat(row["updated_at"])
            if last_disp:
                ld = datetime.datetime.fromisoformat(last_disp)
                if up > ld + datetime.timedelta(minutes=5):
                    disp_n = 0   # progress detected, reset escalation
        except Exception:
            pass

        next_disp_n = disp_n + 1

        # ESCALATION LADDER
        if next_disp_n > ESCALATION_DISPATCH_LIMIT:
            # Reassign to next agent in chain
            chain = REASSIGNMENT_CHAIN.get(agent, ["simon_bately"])
            new_agent = chain[0] if chain else "simon_bately"
            new_reass = reass_n + 1
            handoff = (f"\n[Duke 2026-{now.month:02d}-{now.day:02d}] Reassigned from {agent} → {new_agent} "
                       f"after {disp_n} unanswered dispatches. Reason: stale, no progress.")
            conn.execute(
                "UPDATE tasks SET assigned_to=?, dispatch_count=0, reassignment_count=?, "
                "notes=COALESCE(notes,'')||?, updated_at=? WHERE id=?",
                (new_agent, new_reass, handoff, now.isoformat(), tid)
            )
            actions.append(f"♻ reassigned {agent}→{new_agent}: {title[:45]}")
            agent = new_agent
            next_disp_n = 1

            # If we've reassigned twice already, escalate to Serge directly
            if new_reass >= HUMAN_ESCALATE_REASSIGNS:
                _send_to_agent("simon_bately",
                    f"🚨 ESCALATION from Duke — task has been reassigned {new_reass}× and still not done.\n\n"
                    f"📌 {title}\n"
                    f"Originally: {row['assigned_to']} · Now: {agent}\n"
                    f"Notes: {(row['notes'] or '')[-300:]}\n\n"
                    f"You take this one. Or tell Serge it can't be done.")
                actions.append(f"🚨 escalated to Simon: {title[:45]}")

        # Send the dispatch
        brief = _format_dispatch_brief(row, next_disp_n)
        if _send_to_agent(agent, brief):
            _record_dispatch(conn, tid, agent, next_disp_n)
            actions.append(f"📨 dispatched #{next_disp_n} → {agent}: {title[:45]}")
        else:
            actions.append(f"⚠ dispatch failed → {agent}: {title[:45]}")

    conn.commit()
    conn.close()
    return actions


def handle_stale_tasks(stale):
    """Follow up on tasks that haven't been updated in 48h."""
    if not stale:
        return []

    actions = []
    conn = get_db()
    for task_id, title, agent, updated in stale:
        hours_stale = (datetime.datetime.now() - datetime.datetime.fromisoformat(updated)).total_seconds() / 3600
        note = f"[Duke] No update in {int(hours_stale)}h. Status check requested."
        conn.execute("UPDATE tasks SET notes = COALESCE(notes,'') || ? WHERE id = ?",
                     (f"\n{note}", task_id))
        actions.append(f"Flagged stale: [{agent or '?'}] {title[:50]} ({int(hours_stale)}h)")

        # Publish event to nudge the agent
        publish_event("duke_harmon", "agent_alert", {
            "target": agent or "simon_bately",
            "message": f"Task stale ({int(hours_stale)}h no update): {title[:80]}. Please update status or mark blocked.",
        })
    conn.commit()
    conn.close()
    return actions


def handle_blocked_tasks(blocked):
    """Reassign or escalate blocked tasks."""
    actions = []
    for task_id, title, agent, notes in blocked:
        # Notify Simon for escalation
        publish_event("duke_harmon", "agent_alert", {
            "target": "simon_bately",
            "message": f"BLOCKED task needs escalation: {title[:80]} (assigned: {agent or '?'}). Notes: {(notes or '')[:100]}",
        })
        actions.append(f"Escalated blocked: [{agent or '?'}] {title[:50]}")
    return actions


def create_tasks_from_roadmap():
    """When pending task queue is empty, generate tasks from the roadmap."""
    conn = get_db()

    # Check if we have enough pending work
    pending_count = conn.execute("SELECT count(*) FROM tasks WHERE status='pending'").fetchone()[0]
    in_progress_count = conn.execute("SELECT count(*) FROM tasks WHERE status='in_progress'").fetchone()[0]

    if pending_count + in_progress_count >= 5:
        conn.close()
        return []  # Enough work in pipeline

    # Get roadmap items that are planned or in_progress
    roadmap = conn.execute(
        "SELECT id, title, description, category, priority, assigned_agent FROM baza_roadmap WHERE status IN ('planned','in_progress') ORDER BY CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END LIMIT 3"
    ).fetchall()

    if not roadmap:
        conn.close()
        return []

    actions = []
    for rm_id, rm_title, rm_desc, rm_cat, rm_priority, rm_agent in roadmap:
        # Check if task already exists for this roadmap item
        existing = conn.execute("SELECT id FROM tasks WHERE title LIKE ?", (f"%{rm_title[:30]}%",)).fetchone()
        if existing:
            continue

        # Determine best agent based on category
        if rm_agent:
            agent = rm_agent
        elif rm_cat == "business":
            agent = "simon_bately"
        elif rm_cat == "infrastructure":
            agent = "claw_batto"
        elif rm_cat == "development":
            agent = "claw_batto"
        elif rm_cat == "ai_agents":
            agent = "claw_batto"
        else:
            agent = "simon_bately"

        tid = str(uuid.uuid4())
        task_title = f"Roadmap: {rm_title}"
        task_desc = f"From roadmap item: {rm_desc}\n\nCategory: {rm_cat}\nPriority: {rm_priority}"
        conn.execute(
            "INSERT INTO tasks (id, title, description, status, priority, assigned_to, created_at, notes) VALUES (?,?,?,?,?,?,?,?)",
            (tid, task_title, task_desc, "pending", rm_priority or "medium", agent,
             datetime.datetime.now().isoformat(), "[Duke] Auto-created from roadmap"))

        # Update roadmap item to in_progress
        conn.execute("UPDATE baza_roadmap SET status='in_progress', started_at=datetime('now'), updated_at=datetime('now') WHERE id=? AND status='planned'", (rm_id,))

        actions.append(f"Created task from roadmap: {rm_title[:50]} -> {agent}")

    conn.commit()
    conn.close()
    return actions


def ensure_agents_have_work(workload):
    """Check if any agent has zero pending/in_progress tasks and assign something."""
    idle_agents = [a for a in AGENT_SPECIALTIES if a not in workload or workload.get(a, 0) == 0]
    idle_agents = [a for a in idle_agents if a != "duke_harmon"]  # Duke manages, doesn't self-assign

    if not idle_agents:
        return []

    actions = []
    conn = get_db()
    for agent in idle_agents:
        # Check if there's an unassigned pending task matching their specialty
        unassigned = conn.execute(
            "SELECT id, title FROM tasks WHERE status='pending' AND (assigned_to IS NULL OR assigned_to='') LIMIT 1").fetchone()
        if unassigned:
            conn.execute("UPDATE tasks SET assigned_to=? WHERE id=?", (agent, unassigned[0]))
            actions.append(f"Assigned idle agent {agent}: {unassigned[1][:50]}")
            continue

        # Otherwise, create a maintenance/improvement task based on specialty
        specialty = AGENT_SPECIALTIES[agent]
        titles = {
            "simon_bately": "Review current business priorities and update team directives",
            "claw_batto": "Infrastructure maintenance — check logs, clean up, optimize",
            "phil_hass": "Review financial records and prepare compliance updates",
            "sam_axe": "Create marketing content or update brand materials",
            "rex_valor": "Review leads pipeline and prepare follow-up templates",
            "scout_reeves": "Research latest industry trends and competitor activity",
            "nova_sterling": "Review client communication history and draft outreach",
        }
        title = titles.get(agent, f"Review and improve {specialty}")
        # Dedup: skip if this canned task already exists in the pipeline (any agent — they get reassigned)
        existing = conn.execute(
            "SELECT id FROM tasks WHERE title=? AND status IN ('pending','in_progress','blocked') LIMIT 1",
            (title,)).fetchone()
        if existing:
            continue
        tid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO tasks (id, title, description, status, priority, assigned_to, created_at, notes) VALUES (?,?,?,?,?,?,?,?)",
            (tid, title, f"Auto-generated task to keep {agent} productive. Specialty: {specialty}",
             "pending", "low", agent, datetime.datetime.now().isoformat(),
             "[Duke] Auto-assigned — agent was idle"))
        actions.append(f"Created work for idle agent {agent}: {title[:50]}")

    conn.commit()
    conn.close()
    return actions


def main():
    log.info("Starting task manager cycle...")

    task_stats, proj_stats, stale, blocked, in_prog, pending, workload = collect_status()

    # Phase 1: Direct-dispatch every uncompleted task to its assigned agent.
    # This is Duke's PRIMARY job — drive every task to completion.
    dispatch_actions = dispatch_all_uncompleted()

    # Phase 2: Flag stale (legacy notes for audit trail)
    stale_actions = handle_stale_tasks(stale)

    # Phase 3: Handle blocked tasks
    blocked_actions = handle_blocked_tasks(blocked)

    # Phase 4: Create from roadmap if queue is thin
    roadmap_actions = create_tasks_from_roadmap()

    # Phase 5: Ensure no agent is idle
    idle_actions = ensure_agents_have_work(workload)

    all_actions = dispatch_actions + stale_actions + blocked_actions + roadmap_actions + idle_actions

    # Build status report
    data = f"""TASK MANAGER REPORT — {now()}

TASK BOARD: {dict(task_stats)}
PROJECTS: {dict(proj_stats)}

WORKLOAD BY AGENT:
""" + "\n".join(f"  {a}: {workload.get(a, 0)} active tasks" for a in AGENT_SPECIALTIES)

    if in_prog:
        data += "\n\nIN PROGRESS:\n" + "\n".join(f"  [{t[2] or '?'}] {t[1][:60]}" for t in in_prog)
    if pending:
        data += "\n\nPENDING:\n" + "\n".join(f"  [{p[2] or '?'}] {p[1][:60]} [{p[3]}]" for p in pending[:8])
    if stale:
        data += f"\n\nSTALE TASKS (>48h no update): {len(stale)}"
    if all_actions:
        data += "\n\nACTIONS TAKEN:\n" + "\n".join(f"  - {a}" for a in all_actions)

    # Generate LLM report
    system = f"""You are Duke Harmon — Director of Project Management at AHBCO LLC.
4-hourly task management report for Serge. Plain text, no markdown. Max 25 lines.

You are the task master. Report on:
1. Overall pipeline health — enough work queued? anything stuck?
2. Who's working on what
3. Actions you took (stale flagging, roadmap task creation, reassignments)
4. What Serge needs to decide or approve

Be directive. You own the task board.

{data}"""

    report = ollama_generate(MODEL, system, f"Task management report for {now()}")

    # Save artifact
    save_artifact("proj-baza-empire", f"task_manager_{today()}.md", f"# Task Manager — {now()}\n\n{report}\n\n## Actions\n" +
                  "\n".join(f"- {a}" for a in all_actions) if all_actions else "No actions needed.")

    publish_event("duke_harmon", "report_generated", {
        "type": "task_manager", "summary": report[:200],
        "actions_taken": len(all_actions)
    })

    # Send Serge a SHORT summary — not the full report, not every dispatch.
    # Full report is saved as artifact for anyone who wants details.
    if all_actions:
        summary_lines = [f"📋 Duke — {now()}"]
        summary_lines.append(f"Pipeline: {dict(task_stats)}")
        summary_lines.append(f"Actions: {len(all_actions)}")
        for a in all_actions[:8]:  # max 8 action lines
            summary_lines.append(f"  {a}")
        if len(all_actions) > 8:
            summary_lines.append(f"  ...+{len(all_actions)-8} more (see artifact)")
        send_report("project_tracker", "\n".join(summary_lines), priority="alert", token=AGENT_TOKEN)
    else:
        send_report("project_tracker", f"📋 Duke — {now()}\nAll tasks on track. No actions needed.", priority="alert", token=AGENT_TOKEN)
    log.info(f"Done. Actions taken: {len(all_actions)}")


if __name__ == "__main__":
    with cron_run("project_tracker"):
        main()
