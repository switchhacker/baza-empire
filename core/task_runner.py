#!/usr/bin/env python3
"""
Baza Empire — Autonomous Task Runner
100% local. No Base44. Runs independently via systemd timer or cron.

For each agent with pending/in_progress tasks:
  1. Fetch their tasks from local SQLite
  2. Send task to Ollama with the agent's persona
  3. Parse the output — extract deliverable + completion signal
  4. Mark task completed/in_progress in DB
  5. Save deliverable to tasks notes
  6. Notify Serge via Telegram with what got done

Usage:
  python core/task_runner.py                  # run all agents
  python core/task_runner.py --agent claw_batto  # run one agent
  python core/task_runner.py --dry-run        # show tasks, don't execute
"""
import os
import sys
import logging
import sqlite3
import requests
import argparse
import yaml
import time
from datetime import datetime

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, FRAMEWORK_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(FRAMEWORK_DIR, "configs", "secrets.env"))

from core.task_updater import (
    get_my_tasks, update_task, complete_task,
    start_task, get_project_stats
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [TASK-RUNNER] %(message)s"
)
logger = logging.getLogger(__name__)

OLLAMA_URL     = os.getenv("OLLAMA_URL", "http://localhost:11434")


def is_ollama_busy(timeout: int = 3) -> bool:
    """Check if Ollama is currently processing a request."""
    try:
        r = requests.get(f"{OLLAMA_URL}/api/ps", timeout=timeout)
        if r.ok:
            models = r.json().get("models", [])
            return len(models) > 0  # models listed = actively running inference
        return False
    except:
        return False  # if we can't reach it, assume not busy (will fail at inference time)


def wait_for_ollama(max_wait: int = 120) -> bool:
    """Wait until Ollama is free or timeout. Returns True if free, False if timed out."""
    waited = 0
    while waited < max_wait:
        if not is_ollama_busy():
            return True
        logger.info(f"  Ollama busy — waiting... ({waited}s)")
        time.sleep(10)
        waited += 10
    logger.warning(f"  Ollama still busy after {max_wait}s — skipping task")
    return False
TELEGRAM_TOKEN = os.getenv("TELEGRAM_SIMON_BATELY")
SERGE_CHAT_ID  = os.getenv("SERGE_CHAT_ID", "8551331144")
DB_PATH        = os.path.join(FRAMEWORK_DIR, "dashboard", "baza_projects.db")
CONFIG_PATH    = os.path.join(FRAMEWORK_DIR, "config", "agents.yaml")

# Tasks with these keywords are deliverable by LLM — others need human/tool
LLM_ACTIONABLE = [
    "content", "copy", "write", "draft", "page", "script",
    "document", "policy", "terms", "agreement", "template",
    "research", "competitor", "analysis", "plan", "workflow",
    "process", "logo", "brand", "color", "typography", "icon",
    "brief", "proposal", "email", "intake", "qualification",
    "pipeline", "lead", "invoice", "voicemail", "countdown",
]


def load_agent_configs() -> dict:
    try:
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f)
        return cfg.get("agents", {})
    except Exception as e:
        logger.error(f"Could not load agents.yaml: {e}")
        return {}


def is_llm_actionable(task: dict) -> bool:
    title = (task.get("title", "") + " " + task.get("description", "")).lower()
    return any(kw in title for kw in LLM_ACTIONABLE)


def run_task_with_llm(agent_id: str, agent_cfg: dict, task: dict) -> dict:
    """
    Send a task to Ollama with the agent's persona.
    Returns {"success": bool, "output": str, "completed": bool}
    """
    model       = agent_cfg.get("model", "qwen2.5:14b")
    agent_name  = agent_cfg.get("name", agent_id)
    system_base = agent_cfg.get("system_prompt", f"You are {agent_name}.")

    system = (
        f"{system_base}\n\n"
        "TASK EXECUTION MODE:\n"
        "You have been assigned a task. Execute it fully and produce the real deliverable.\n"
        "At the end of your response, write exactly one of these on its own line:\n"
        "  TASK_COMPLETE — if the task is fully done\n"
        "  TASK_IN_PROGRESS — if you made progress but need more work\n"
        "  TASK_BLOCKED: [reason] — if you cannot proceed\n\n"
        "Plain text only. No markdown headers. No ** bold. Use emoji for structure."
    )

    user_msg = (
        f"Execute this task now:\n\n"
        f"Title: {task['title']}\n"
        f"Description: {task.get('description', '')}\n"
        f"Due: {task.get('due_date', 'ASAP')}\n"
        f"Priority: {task.get('priority', 'medium')}\n\n"
        f"Produce the full deliverable. Be specific and complete."
    )

    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": model,
                "stream": False,
                "options": {"num_predict": 800, "temperature": 0.3},
                "messages": [
                    {"role": "system",  "content": system},
                    {"role": "user",    "content": user_msg},
                ]
            },
            timeout=90
        )
        resp.raise_for_status()
        output = resp.json()["message"]["content"].strip()

        # Parse completion signal
        completed  = "TASK_COMPLETE" in output
        blocked    = "TASK_BLOCKED:" in output
        in_progress = "TASK_IN_PROGRESS" in output

        # Extract blocked reason if any
        block_reason = ""
        if blocked:
            for line in output.split("\n"):
                if "TASK_BLOCKED:" in line:
                    block_reason = line.split("TASK_BLOCKED:", 1)[-1].strip()
                    break

        # Clean signal lines from output before saving as notes
        clean_output = "\n".join(
            line for line in output.split("\n")
            if not any(sig in line for sig in ["TASK_COMPLETE", "TASK_IN_PROGRESS", "TASK_BLOCKED:"])
        ).strip()

        return {
            "success":      True,
            "output":       clean_output,
            "completed":    completed,
            "in_progress":  in_progress,
            "blocked":      blocked,
            "block_reason": block_reason,
        }

    except Exception as e:
        logger.error(f"LLM error for {agent_id} task {task['id'][:8]}: {e}")
        return {"success": False, "output": str(e), "completed": False}


def notify_serge(message: str):
    if not TELEGRAM_TOKEN:
        logger.warning("No Telegram token — skipping notify")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        for chunk in [message[i:i+4000] for i in range(0, len(message), 4000)]:
            requests.post(url, json={"chat_id": SERGE_CHAT_ID, "text": chunk}, timeout=15)
    except Exception as e:
        logger.error(f"Telegram notify error: {e}")



def _save_artifact(agent_id: str, task: dict, content: str):
    """Save completed task deliverable to artifacts directory."""
    try:
        proj_id  = task.get("project_id", "shared")
        title    = task.get("title", "artifact").replace("/", "-").replace(" ", "_")[:40]
        ts       = datetime.now().strftime("%Y%m%d_%H%M")
        filename = f"{agent_id}_{ts}_{title}.txt"
        art_dir  = os.path.join(FRAMEWORK_DIR, "dashboard", "artifacts", proj_id)
        os.makedirs(art_dir, exist_ok=True)
        with open(os.path.join(art_dir, filename), "w", encoding="utf-8") as f:
            f.write(f"Task: {task.get('title')}\n")
            f.write(f"Agent: {agent_id}\n")
            f.write(f"Completed: {datetime.now().isoformat()}\n")
            f.write("=" * 60 + "\n\n")
            f.write(content)
        logger.info(f"  📁 Artifact saved: {filename}")
    except Exception as e:
        logger.warning(f"  Artifact save failed: {e}")


def run_agent_tasks(agent_id: str, agent_cfg: dict, dry_run: bool = False, task_id: str = None) -> list:
    """Run all pending tasks for one agent. Returns list of result dicts."""
    if task_id:
        # Single task mode — fetch just this task
        from core.task_updater import get_task_by_id
        t = get_task_by_id(task_id)
        tasks = [t] if t else []
    else:
        tasks = get_my_tasks(agent_id, status="pending")
        # Add in_progress only if not already in list (avoid duplicates)
        in_prog = get_my_tasks(agent_id, status="in_progress")
        existing_ids = {t["id"] for t in tasks}
        tasks += [t for t in in_prog if t["id"] not in existing_ids]

    # Filter out completed tasks — never re-run them
    tasks = [t for t in tasks if t.get("status") != "completed"]

    if not tasks:
        logger.info(f"[{agent_id}] No tasks to run.")
        return []

    agent_name = agent_cfg.get("name", agent_id)
    results = []

    for task in tasks:
        task_id    = task["id"]
        task_title = task["title"]

        logger.info(f"[{agent_id}] Task [{task_id}]: {task_title[:60]}")

        if dry_run:
            actionable = is_llm_actionable(task)
            logger.info(f"  DRY RUN — actionable: {actionable}")
            results.append({"task": task_title, "dry_run": True, "actionable": actionable})
            continue

        if not is_llm_actionable(task):
            logger.info(f"  Skipping non-LLM task: {task_title[:50]}")
            # Mark in_progress so it shows activity
            start_task(task_id, notes="Requires external action or tool — marked in progress")
            continue

        # Wait for Ollama to be free before running
        if not wait_for_ollama(max_wait=120):
            logger.warning(f"  Skipping {task_title[:40]} — Ollama busy")
            results.append({"task": task_title, "status": "skipped"})
            continue

        # Mark in_progress before running
        start_task(task_id)

        result = run_task_with_llm(agent_id, agent_cfg, task)

        if result["success"]:
            # Save output as notes (truncated to fit DB)
            notes = result["output"][:500]

            if result["completed"]:
                complete_task(task_id, notes=notes)
                logger.info(f"  ✅ COMPLETED: {task_title[:50]}")
                # Save full deliverable as artifact
                _save_artifact(agent_id, task, result["output"])
                results.append({"task": task_title, "status": "completed", "output": notes})

            elif result["blocked"]:
                update_task(task_id, {
                    "status": "blocked",
                    "notes": f"BLOCKED: {result['block_reason']}"
                })
                logger.info(f"  🔴 BLOCKED: {task_title[:50]} — {result['block_reason']}")
                results.append({"task": task_title, "status": "blocked", "reason": result["block_reason"]})

            else:
                update_task(task_id, {"status": "in_progress", "notes": notes})
                logger.info(f"  🟡 IN PROGRESS: {task_title[:50]}")
                results.append({"task": task_title, "status": "in_progress", "output": notes})
        else:
            logger.error(f"  LLM failed for {task_title[:50]}: {result['output'][:100]}")
            results.append({"task": task_title, "status": "error", "output": result["output"]})

        # Brief pause between tasks so Ollama isn't hammered
        time.sleep(5)

    return results


def build_summary_message(all_results: dict) -> str:
    """Build Telegram notification with what got done."""
    stats = get_project_stats()
    now   = datetime.now().strftime("%I:%M %p")

    lines = [
        f"━━━━━━━━━━━━━━━━",
        f"⚡ Task Runner — {now}",
        f"━━━━━━━━━━━━━━━━",
        f"📊 {stats['progress_pct']}% done ({stats['completed']}/{stats['total']} tasks)",
        "",
    ]

    name_map = {
        "claw_batto": "Claw", "sam_axe": "Sam", "phil_hass": "Phil",
        "simon_bately": "Simon", "duke_harmon": "Duke", "rex_valor": "Rex",
        "scout_reeves": "Scout", "nova_sterling": "Nova",
    }

    for agent_id, results in all_results.items():
        if not results:
            continue
        name = name_map.get(agent_id, agent_id)
        completed   = [r for r in results if r.get("status") == "completed"]
        blocked     = [r for r in results if r.get("status") == "blocked"]
        in_progress = [r for r in results if r.get("status") == "in_progress"]

        if completed or blocked:
            lines.append(f"👤 {name}:")
            for r in completed:
                lines.append(f"  ✅ {r['task'][:55]}")
            for r in blocked:
                lines.append(f"  🔴 {r['task'][:40]} — {r.get('reason','')[:30]}")
            for r in in_progress:
                lines.append(f"  🟡 {r['task'][:55]}")
            lines.append("")

    if stats["blocked"] > 0:
        lines.append(f"⚠️ {stats['blocked']} task(s) blocked — check dashboard")

    lines.append("━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent",   help="Run only this agent (e.g. claw_batto)")
    parser.add_argument("--task-id", help="Run only this specific task ID")
    parser.add_argument("--dry-run", action="store_true", help="Show tasks without executing")
    args = parser.parse_args()

    agents = load_agent_configs()
    if not agents:
        logger.error("No agents found in config — aborting")
        sys.exit(1)

    if args.agent:
        if args.agent not in agents:
            logger.error(f"Agent '{args.agent}' not found in config")
            sys.exit(1)
        agents = {args.agent: agents[args.agent]}

    all_results = {}
    for agent_id, agent_cfg in agents.items():
        logger.info(f"Running tasks for: {agent_id}")
        results = run_agent_tasks(agent_id, agent_cfg, dry_run=args.dry_run, task_id=getattr(args, 'task_id', None))
        all_results[agent_id] = results

    if not args.dry_run:
        # Only notify if something actually happened
        any_results = any(r for r in all_results.values())
        if any_results:
            msg = build_summary_message(all_results)
            notify_serge(msg)
            logger.info("Summary sent to Serge.")
        else:
            logger.info("No tasks ran — nothing to notify.")


if __name__ == "__main__":
    main()
