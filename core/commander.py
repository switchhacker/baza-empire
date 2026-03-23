"""
Baza Empire — Simon Commander Module
-------------------------------------
Simon receives orders from Serge, builds a task brief, dispatches
instructions to the right agents via Telegram Bot API, collects their
responses via Redis, and reports a final summary back to Serge.
"""

import os
import json
import time
import logging
import requests
import redis as redis_lib

logger = logging.getLogger(__name__)

# ─── Agent registry ───────────────────────────────────────────────────────────
# Maps agent_id to their Telegram bot token env var and chat ID env var
AGENT_REGISTRY = {
    'claw_batto': {
        'token_env': 'TELEGRAM_CLAW_BATTO',
        'name': 'Claw Batto',
    },
    'phil_hass': {
        'token_env': 'TELEGRAM_PHIL_HASS',
        'name': 'Phil Hass',
    },
    'sam_axe': {
        'token_env': 'TELEGRAM_SAM_AXE',
        'name': 'Sam Axe',
    },
}

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


class SimonCommander:
    """
    Plugs into Simon's BazaAgent instance.
    Gives Simon the ability to:
      - Dispatch tasks to other agents
      - Wait for and collect their responses via Redis
      - Build a final briefing report for Serge
    """

    def __init__(self, redis_client: redis_lib.Redis, serge_chat_id: str, simon_token: str):
        self.redis = redis_client
        self.serge_chat_id = serge_chat_id  # Serge's Telegram chat ID with Simon
        self.simon_token = simon_token

    # ─── Send a message to an agent's private chat with Simon ────────────────

    def dispatch_to_agent(self, agent_id: str, instruction: str, task_id: str) -> bool:
        """
        Sends an instruction to a specific agent bot via Telegram.
        The instruction is prepended with task metadata so the agent
        knows to report back to Simon via Redis when done.
        """
        agent = AGENT_REGISTRY.get(agent_id)
        if not agent:
            logger.error(f"Unknown agent: {agent_id}")
            return False

        token = os.environ.get(agent['token_env'])
        if not token:
            logger.error(f"No token for {agent_id}")
            return False

        # Get the agent's stored chat ID with Serge (shared inbox pattern)
        chat_id = self.redis.get(f"agent:{agent_id}:serge_chat_id")
        if not chat_id:
            logger.error(f"No chat_id stored for {agent_id}. Agent must message Serge first.")
            return False

        full_message = (
            f"[TASK:{task_id}] Simon says:\n\n"
            f"{instruction}\n\n"
            f"When complete, respond with your full report. Begin with: REPORT:{task_id}:"
        )

        url = TELEGRAM_API.format(token=token, method="sendMessage")
        resp = requests.post(url, json={
            "chat_id": chat_id,
            "text": full_message,
            "parse_mode": "HTML"
        }, timeout=15)

        if resp.ok:
            logger.info(f"Dispatched task {task_id} to {agent['name']}")
            # Store pending task in Redis
            self.redis.hset(f"task:{task_id}", mapping={
                "agent_id": agent_id,
                "agent_name": agent['name'],
                "instruction": instruction,
                "status": "pending",
                "dispatched_at": str(time.time())
            })
            self.redis.expire(f"task:{task_id}", 3600)
            return True
        else:
            logger.error(f"Failed to dispatch to {agent_id}: {resp.text}")
            return False

    # ─── Parse incoming agent reports ────────────────────────────────────────

    def receive_report(self, text: str, agent_id: str) -> bool:
        """
        Called when an agent sends a message starting with REPORT:{task_id}:
        Stores the report in Redis and checks if all tasks for the job are done.
        """
        if not text.startswith("REPORT:"):
            return False

        parts = text.split(":", 2)
        if len(parts) < 3:
            return False

        task_id = parts[1]
        report_body = parts[2].strip()

        # Store the report
        self.redis.hset(f"task:{task_id}", mapping={
            "status": "complete",
            "report": report_body,
            "completed_at": str(time.time())
        })

        logger.info(f"Received report for task {task_id} from {agent_id}")

        # Check if parent job is fully complete
        job_id = self.redis.get(f"task:{task_id}:job_id")
        if job_id:
            self._check_job_complete(job_id)

        return True

    # ─── Job orchestration ───────────────────────────────────────────────────

    def create_job(self, job_id: str, task_assignments: dict) -> None:
        """
        task_assignments = {
            'claw_batto': 'instruction for claw...',
            'phil_hass':  'instruction for phil...',
            'sam_axe':    'instruction for sam...',
        }
        Creates task IDs, dispatches all, tracks in Redis.
        """
        task_ids = []
        for agent_id, instruction in task_assignments.items():
            task_id = f"{job_id}:{agent_id}"
            task_ids.append(task_id)
            self.redis.set(f"task:{task_id}:job_id", job_id, ex=3600)
            self.dispatch_to_agent(agent_id, instruction, task_id)

        self.redis.hset(f"job:{job_id}", mapping={
            "task_ids": json.dumps(task_ids),
            "status": "in_progress",
            "created_at": str(time.time())
        })
        self.redis.expire(f"job:{job_id}", 3600)
        logger.info(f"Job {job_id} created with {len(task_ids)} tasks")

    def _check_job_complete(self, job_id: str) -> None:
        """Check if all tasks in a job are done — if so, compile and send report to Serge."""
        job = self.redis.hgetall(f"job:{job_id}")
        if not job:
            return

        task_ids = json.loads(job.get("task_ids", "[]"))
        reports = {}
        all_done = True

        for task_id in task_ids:
            task = self.redis.hgetall(f"task:{task_id}")
            if task.get("status") != "complete":
                all_done = False
                break
            reports[task.get("agent_name", task_id)] = task.get("report", "(no report)")

        if all_done:
            self._send_final_report(job_id, reports)
            self.redis.hset(f"job:{job_id}", "status", "complete")

    def _send_final_report(self, job_id: str, reports: dict) -> None:
        """Compile all agent reports and send Simon's summary to Serge."""
        lines = [f"<b>Mission Complete — Job {job_id}</b>\n"]
        for agent_name, report in reports.items():
            lines.append(f"<b>{agent_name}:</b>\n{report}\n")

        lines.append("\n<b>Simon:</b> All tasks completed. Standing by for next orders.")
        full_report = "\n".join(lines)

        url = TELEGRAM_API.format(token=self.simon_token, method="sendMessage")
        requests.post(url, json={
            "chat_id": self.serge_chat_id,
            "text": full_report,
            "parse_mode": "HTML"
        }, timeout=15)

        logger.info(f"Final report for job {job_id} sent to Serge")

    # ─── Store agent chat IDs ────────────────────────────────────────────────

    def register_agent_chat(self, agent_id: str, chat_id: str) -> None:
        """Called when an agent receives a message from Serge — stores their chat ID."""
        self.redis.set(f"agent:{agent_id}:serge_chat_id", chat_id, ex=86400 * 30)
        logger.info(f"Registered chat_id {chat_id} for {agent_id}")
