"""
Baza Empire — Duke Harmon
Project Manager & Deadline Keeper
100% local SQLite. No hallucinated task statuses.
"""
import re
import asyncio
import logging
import sqlite3
import os
from datetime import datetime, date
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes
from core.base_agent import BaseAgent
from core.memory import save_message, get_history
from core.task_updater import get_all_tasks, get_project_stats, update_task

logger = logging.getLogger(__name__)

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH       = os.path.join(FRAMEWORK_DIR, "dashboard", "baza_projects.db")
MAX_HISTORY   = 8
LAUNCH_DATE   = date(2026, 4, 1)

STATUS_KEYWORDS = [
    "status", "progress", "tasks", "deadline", "overdue", "blocked",
    "update", "report", "tracker", "where are we", "behind", "on track",
    "briefing", "summary", "how are we", "what's left", "whats left",
]

NAME_MAP = {
    "claw_batto":    "CLAW BATTO",
    "sam_axe":       "SAM AXE",
    "phil_hass":     "PHIL HASS",
    "simon_bately":  "SIMON BATELY",
    "duke_harmon":   "DUKE HARMON",
    "rex_valor":     "REX VALOR",
    "scout_reeves":  "SCOUT REEVES",
    "nova_sterling": "NOVA STERLING",
}

STATUS_ICON = {
    "completed":   "🟢",
    "in_progress": "🟡",
    "blocked":     "🔴",
    "pending":     "⚪",
}


class DukeHarmon(BaseAgent):
    AGENT_ID = "duke_harmon"
    MODEL    = "qwen2.5:14b"
    TOKEN_ENV = "TELEGRAM_DUKE_HARMON"
    USE_GPU_POOL = True

    # ── Real data from SQLite ─────────────────────────────────────────────────

    def _build_status_report(self) -> str:
        """Build project status report directly from local SQLite. No LLM needed."""
        try:
            tasks  = get_all_tasks()
            stats  = get_project_stats()
            today  = date.today()
            days_left = (LAUNCH_DATE - today).days

            # Group by agent
            by_agent = {}
            for t in tasks:
                a = t["assigned_to"]
                by_agent.setdefault(a, []).append(t)

            today_str = today.strftime("%Y-%m-%d")
            overdue   = [t for t in tasks
                         if t.get("due_date") and t["due_date"] < today_str
                         and t["status"] not in ("completed",)]
            blocked   = [t for t in tasks if t["status"] == "blocked"]

            lines = [
                "━━━━━━━━━━━━━━━━",
                f"📦 PROJECT STATUS — ahb123.com Website",
                f"🗓 Launch: April 1, 2026 | Days Left: {days_left}",
                f"📊 Progress: {stats['progress_pct']}% ({stats['completed']}/{stats['total']} tasks done)",
                "━━━━━━━━━━━━━━━━",
                "",
            ]

            for agent_id, agent_tasks in sorted(by_agent.items()):
                name = NAME_MAP.get(agent_id, agent_id.upper())
                lines.append(f"👤 {name}")
                for t in agent_tasks:
                    icon    = STATUS_ICON.get(t["status"], "⚪")
                    due     = f" — due {t['due_date']}" if t.get("due_date") else ""
                    note    = f"\n    📝 {t['notes'][:80]}" if t.get("notes") else ""
                    overdue_flag = " 🔥" if t in overdue else ""
                    lines.append(f"  {icon} {t['title']}{due}{overdue_flag}{note}")
                lines.append("")

            lines.append("━━━━━━━━━━━━━━━━")
            if blocked:
                lines.append(f"⚠️ BLOCKERS: {len(blocked)}")
                for b in blocked:
                    lines.append(f"  🔴 {b['title']} — {b.get('notes','')[:60]}")
            else:
                lines.append("⚠️ BLOCKERS: none")

            if overdue:
                lines.append(f"🔥 OVERDUE: {len(overdue)}")
                for o in overdue:
                    lines.append(f"  ⏰ {o['title']} ({NAME_MAP.get(o['assigned_to'], o['assigned_to'])}) — due {o['due_date']}")
            else:
                lines.append("🔥 OVERDUE: none")

            lines.append("━━━━━━━━━━━━━━━━")
            return "\n".join(lines)

        except Exception as e:
            return f"Duke error reading task DB: {e}"

    def _get_task_db_context(self) -> str:
        """Inject real task data into LLM context so it never hallucinates statuses."""
        try:
            tasks = get_all_tasks()
            stats = get_project_stats()
            today = date.today()
            days_left = (LAUNCH_DATE - today).days
            lines = [
                f"REAL TASK DATA (from local DB — use ONLY these values, never invent):",
                f"Total: {stats['total']} | Done: {stats['completed']} | In Progress: {stats['in_progress']} | Blocked: {stats['blocked']} | Progress: {stats['progress_pct']}%",
                f"Days to launch: {days_left}",
                "",
            ]
            for t in tasks:
                lines.append(
                    f"[{t['id'][:8]}] {t['assigned_to']} | {t['status']} | {t['title']} | due: {t.get('due_date','?')}"
                )
            return "\n".join(lines)
        except:
            return "Task data unavailable."

    # ── Command detection ─────────────────────────────────────────────────────

    def _is_status_request(self, text: str) -> bool:
        return any(kw in text.lower() for kw in STATUS_KEYWORDS)

    def _parse_update_command(self, text: str):
        """
        Detect: update <task_id_prefix> <status>
        Returns (task_id_prefix, status) or (None, None)
        """
        m = re.match(
            r'^(?:update|mark|set)\s+([a-f0-9\-]{6,})\s+(completed?|done|in.?progress|blocked|pending)',
            text.strip(), re.IGNORECASE
        )
        if m:
            status_raw = m.group(2).lower()
            status_map = {
                "complete": "completed", "completed": "completed", "done": "completed",
                "in progress": "in_progress", "inprogress": "in_progress",
                "blocked": "blocked", "pending": "pending",
            }
            status = status_map.get(status_raw.replace("-", "").replace(" ", ""), status_raw)
            return m.group(1), status
        return None, None

    def _find_task_by_prefix(self, prefix: str) -> dict:
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM tasks WHERE id LIKE ?", (prefix + "%",)).fetchone()
            conn.close()
            return dict(row) if row else {}
        except:
            return {}

    # ── System prompt ─────────────────────────────────────────────────────────

    def build_system_prompt(self, extra: str = "") -> str:
        task_context = self._get_task_db_context()
        extra_instructions = f"""
You are Duke Harmon — Project Manager and Deadline Keeper for the Baza Empire and AHBCO LLC.
You report directly to Serge (Master Orchestrator).

== YOUR ROLE ==
Track tasks, enforce deadlines, flag blockers, coordinate the team.
ahb123.com launches April 1, 2026. Keep everything on track.

== TEAM ==
Simon (content/biz), Claw (dev), Sam (design), Phil (legal), Rex (leads), Scout (research), Nova (client chat)

== REAL TASK DATA — USE ONLY THIS, NEVER INVENT ==
{task_context}

== FORMATTING RULES ==
NO markdown. NO ### headers. NO ** bold. Plain text + emoji only.
Use ━━━ as dividers. Status icons: 🟢 done  🟡 in progress  🔴 blocked  ⚪ pending  🔥 overdue
"""
        # Call grandparent to avoid double task injection from BaseAgent
        from core.context_mixin import ContextMixin
        prompt = ContextMixin.get_system_prompt(self)
        return prompt + "\n\n" + extra_instructions

    # ── Message handler ───────────────────────────────────────────────────────

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        text    = update.message.text or ""
        if not text.strip():
            return

        logger.info(f"[duke_harmon] Message: {text[:80]}")
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        save_message(chat_id, self.AGENT_ID, "user", text)

        # ── Direct status report — no LLM needed ─────────────────────────────
        if self._is_status_request(text):
            response = self._build_status_report()
            await self._send_response(context.bot, chat_id, response)
            save_message(chat_id, self.AGENT_ID, "assistant", response)
            return

        # ── Update command: "update <id> completed" ───────────────────────────
        prefix, status = self._parse_update_command(text)
        if prefix and status:
            task = self._find_task_by_prefix(prefix)
            if task:
                update_task(task["id"], {"status": status})
                response = f"✅ Updated [{prefix[:8]}] {task['title']} → {status}"
            else:
                response = f"❌ No task found with ID starting with {prefix}"
            await self._send_response(context.bot, chat_id, response)
            save_message(chat_id, self.AGENT_ID, "assistant", response)
            return

        # ── LLM for everything else ───────────────────────────────────────────
        history  = get_history(chat_id, self.AGENT_ID, limit=MAX_HISTORY)
        messages = [{"role": h["role"], "content": h["content"]} for h in history]
        loop     = asyncio.get_event_loop()

        system   = self.build_system_prompt()
        messages_with_user = messages + [{
            "role": "user",
            "content": (
                f"{text}\n\n"
                "[RULES: Plain text only. No markdown. No ** or ###. "
                "Use ONLY real task data injected in your system prompt. Never invent statuses or names.]"
            )
        }]

        response = await loop.run_in_executor(None, self.llm_chat, messages_with_user, system)
        if not response or not isinstance(response, str):
            response = self._build_status_report()

        save_message(chat_id, self.AGENT_ID, "assistant", response)
        self._auto_remember(chat_id, text, response)
        await self._send_response(context.bot, chat_id, response)

    def _auto_remember(self, chat_id: int, user_msg: str, agent_reply: str):
        super()._auto_remember(chat_id, user_msg, agent_reply)
        m = re.search(r'(due|deadline|by)[:\s]+([A-Za-z]+ \d+|\d{4}-\d{2}-\d{2})', user_msg, re.IGNORECASE)
        if m:
            self.remember("last_deadline_mentioned", m.group(2), "deadlines")


if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO)
    agent = DukeHarmon()
    asyncio.run(agent.run())
