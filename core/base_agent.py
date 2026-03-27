"""
Baza Empire — Base Agent
--------------------------
All agents inherit from this. It wires together:
  - Persistent context (memory, identity, empire knowledge, summaries)
  - Skills engine (parse ##SKILL:## calls in LLM output, execute them)
  - Ollama LLM with pooled GPU access
  - Conversation history (per-agent, per-chat)
  - Auto-summarization (every N messages, compress history to a summary)
  - Task journal (every action logged)

Usage:
    class ClawBatto(BaseAgent):
        AGENT_ID = "claw_batto"
        MODEL = "qwen2.5:14b"
        TOKEN_ENV = "TELEGRAM_CLAW_BATTO"

    agent = ClawBatto()
    await agent.run()
"""

import os
import re
import asyncio
import logging
import json
import time
from typing import Optional

import httpx
from telegram import Update, Bot
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from telegram.constants import ChatAction

from core.ollama_client import chat_stream_pooled, chat_stream
from core.context_mixin import ContextMixin
from core.memory import (
    init_db, save_message, get_history, get_active_task, set_task, complete_task
)
from core.task_updater import AgentTaskManager
from skills.shared.save_artifact import save_artifact as _save_artifact_fn

logger = logging.getLogger(__name__)

# After this many messages in a session, trigger auto-summarization
AUTO_SUMMARIZE_AFTER = 15

# Max history messages to feed the LLM (keep context window manageable)
MAX_HISTORY = 20


class BaseAgent(ContextMixin):
    """
    Base class for all Baza Empire agents.
    Subclasses set:
        AGENT_ID  — matches context DB (e.g. "claw_batto")
        MODEL     — Ollama model name (e.g. "qwen2.5:14b")
        TOKEN_ENV — env var name for Telegram bot token
    """
    AGENT_ID: str = "base"
    MODEL: str = "qwen2.5:14b"
    TOKEN_ENV: str = ""

    # Set True to use GPU pool (both GPUs shared), False to use AMD only
    USE_GPU_POOL: bool = True

    def __init__(self):
        self.agent_id = self.AGENT_ID
        self.init_context()           # ContextMixin: sets up skills, loads identity
        init_db()                     # Legacy memory/tasks tables
        self._message_counts: dict = {}   # chat_id → message count this session
        self.tasks = AgentTaskManager(self.AGENT_ID)   # local SQLite task manager

    def save_artifact(self, filename: str, content: str, project_id: str = "shared", task_id: str = "") -> dict:
        """
        Save any file (html/json/py/md/sh/yaml/csv/etc.) to the dashboard artifacts.
        Agents call this to persist deliverables Serge can view and download.

        Example:
            self.save_artifact("report.html", "<html>...</html>", project_id="proj-ahb123")
            self.save_artifact("config.json", json.dumps(data), project_id="proj-baza-empire")
            self.save_artifact("setup.py", code_str)
        """
        return _save_artifact_fn(
            filename=filename,
            content=content,
            project_id=project_id,
            agent_id=self.AGENT_ID,
            task_id=task_id,
        )

    # ── System Prompt ─────────────────────────────────────────────────────────

    def build_system_prompt(self, extra: str = "") -> str:
        """
        Full system prompt = base identity + live context injection + task state.
        Optionally append extra instructions per-call.
        """
        prompt = self.get_system_prompt()  # from ContextMixin
        # Always inject current task state so agent knows what to work on
        try:
            task_summary = self.tasks.summary_text()
            prompt += f"\n\n== YOUR CURRENT TASKS (live from local DB) ==\n{task_summary}\n== END TASKS ==\n"
            prompt += (
                "\n\nCRITICAL: When you complete a task, you MUST call the update_task skill with status=completed. "
                "When you start working on something, call update_task with status=in_progress. "
                "Use the task ID shown in brackets above (e.g. [a1b2c3d4]). "
                "This is how your work gets tracked. Do not skip this."
            )
        except Exception:
            pass

        # Inject web search + scraping capability docs
        prompt += (
            "\n\n== WEB TOOLS ==\n"
            "self.web_search(query, n=5) → list of {title,url,snippet} from DuckDuckGo\n"
            "self.scrape_page(url, max_chars=4000) → {success,title,text} clean page text\n"
            "Use web_search FIRST to find relevant URLs, then scrape_page to read them.\n"
            "Always cite URLs when using web data.\n"
            "== END WEB TOOLS =="
        )

        # Inject artifact creation capability docs
        prompt += (
            "\n\n== CREATING FILES & ARTIFACTS ==\n"
            "You can save files of any type to the project dashboard so Serge can view/download them.\n"
            "Call: ##SKILL:artifact_save{\"filename\":\"name.ext\",\"content\":\"...\",\"project_id\":\"proj-id\"}##\n"
            "Supported: .html .json .py .md .sh .yaml .csv .txt .js .ts .css .sql .log .svg .conf .toml\n"
            "Examples:\n"
            "  ##SKILL:artifact_save{\"filename\":\"summary.md\",\"content\":\"# Summary\\n...\",\"project_id\":\"proj-ahb123\"}##\n"
            "  ##SKILL:artifact_save{\"filename\":\"config.json\",\"content\":\"{}\",\"project_id\":\"proj-baza-empire\"}##\n"
            "ALWAYS save deliverables as real files — do not just describe them in chat.\n"
            "== END ARTIFACTS =="
        )

        # Inject dynamic skill creation docs
        prompt += (
            "\n\n== DYNAMIC TOOLS — CREATE ANY SKILL YOU NEED ==\n"
            "If you need a tool that doesn't exist, CREATE IT on the spot:\n"
            "##SKILL:create_skill{\"name\":\"tool_name\",\"description\":\"what it does\","
            "\"code\":\"#!/usr/bin/env python3\\nimport os,json\\nargs=json.loads(os.environ.get('SKILL_ARGS','{}'))\\n# your code\\nprint(result)\"}##\n"
            "Rules: name must be snake_case. Code runs as subprocess. Read args from SKILL_ARGS env var. Print result to stdout.\n"
            "You can create: API callers, system queries, file processors, calculators, scrapers — anything.\n"
            "After creating, immediately call it: ##SKILL:tool_name{\"arg\":\"value\"}##\n"
            "== END DYNAMIC TOOLS =="
        )

        if extra:
            prompt += f"\n\n{extra}"
        return prompt

    def web_search(self, query: str, n: int = 5) -> list:
        """
        Search the web via DuckDuckGo. Returns list of {title, url, snippet}.
        Example: results = self.web_search("PA HIC license renewal 2025")
        """
        result = self.skills.run("web_search", {"query": query, "n": n, "output": "json"})
        if result.get("success"):
            try:
                import json as _json
                data = _json.loads(result.get("output", "{}"))
                return data.get("results", [])
            except Exception:
                pass
        return []

    def scrape_page(self, url: str, max_chars: int = 4000) -> dict:
        """
        Fetch and extract clean text from a URL.
        Example: page = self.scrape_page("https://www.attorneygeneral.gov/...")
        Returns: {success, title, text, url, chars}
        """
        result = self.skills.run("scrape_page", {"url": url, "max_chars": max_chars, "output": "json"})
        if result.get("success"):
            try:
                import json as _json
                return _json.loads(result.get("output", "{}"))
            except Exception:
                pass
        return {"success": False, "error": result.get("output", "skill error")}

    # ── LLM Call ──────────────────────────────────────────────────────────────

    def llm_chat(self, messages: list, system_prompt: str) -> str:
        """
        Run an LLM inference. Streams internally, returns full response string.
        Uses GPU pool if USE_GPU_POOL, otherwise AMD only.
        """
        full = ""
        if self.USE_GPU_POOL:
            for chunk in chat_stream_pooled(self.MODEL, messages, system_prompt, self.AGENT_ID):
                full += chunk
        else:
            from core.ollama_client import OLLAMA_AMD_URL
            for chunk in chat_stream(self.MODEL, messages, system_prompt, OLLAMA_AMD_URL):
                full += chunk
        return full.strip()


    # ── Task Command Interception ─────────────────────────────────────────────

    CREATE_PATTERNS = [
        r'create\s+(?:a\s+)?(?:new\s+)?task[:\s]+(.+?)(?:\s+for\s+(\w+))?$',
        r'add\s+(?:a\s+)?(?:new\s+)?task[:\s]+(.+?)(?:\s+for\s+(\w+))?$',
        r'new\s+task[:\s]+(.+?)(?:\s+for\s+(\w+))?$',
        r'(?:simon|claw|sam|phil|rex|duke|scout|nova)[,\s]+create\s+(?:a\s+)?(?:new\s+)?task[:\s]+(.+)',
    ]

    AGENT_NAMES = {
        'simon': 'simon_bately', 'claw': 'claw_batto', 'sam': 'sam_axe',
        'phil': 'phil_hass', 'rex': 'rex_valor', 'duke': 'duke_harmon',
        'scout': 'scout_reeves', 'nova': 'nova_sterling',
    }

    def _try_create_task_from_message(self, text: str) -> str | None:
        """
        Detect "create task: X" or "new task: X" in user message.
        Writes to DB immediately. Returns confirmation string or None.
        """
        import re
        t = text.strip()

        # Pattern: "<agent>, create a new task <title>"
        m = re.match(
            r'(?:simon|claw|sam|phil|rex|duke|scout|nova)[,\s]+create\s+(?:a\s+)?(?:new\s+)?task\s+(.+)',
            t, re.IGNORECASE
        )
        if not m:
            # Pattern: "create task: <title>"
            m = re.match(r'(?:create|add|new)\s+(?:a\s+)?(?:new\s+)?task[:\s]+(.+)', t, re.IGNORECASE)

        if not m:
            return None

        raw_title = m.group(1).strip()

        # Check if another agent is mentioned at the end: "...for claw"
        assign_to = self.AGENT_ID
        assign_m = re.search(r'\s+(?:for|assign(?:ed)?\s+to)\s+(\w+)\s*$', raw_title, re.IGNORECASE)
        if assign_m:
            name_key = assign_m.group(1).lower()
            if name_key in self.AGENT_NAMES:
                assign_to = self.AGENT_NAMES[name_key]
                raw_title = raw_title[:assign_m.start()].strip()

        # Infer project from keywords
        project_id = 'proj-ahb123'
        lower = raw_title.lower()
        if any(k in lower for k in ['baza', 'mining', 'node', 'firmware', 'rig', 'agent']):
            project_id = 'proj-baza-empire'

        task_id = self.tasks.add(
            project_id=project_id,
            title=raw_title,
            description=raw_title,
            priority='medium',
        )
        # If assigned to someone else, update that field
        if assign_to != self.AGENT_ID:
            from core.task_updater import update_task
            update_task(task_id, {'assigned_to': assign_to})

        if task_id:
            assignee_display = assign_to.replace('_', ' ').title()
            return (
                f"Task created and saved to project board\n"
                f"ID: {task_id[:8]}\n"
                f"Title: {raw_title}\n"
                f"Assigned to: {assignee_display}\n"
                f"Project: {project_id}\n"
                f"Status: pending\n"
                f"Use the dashboard to queue it to an agent, or I can start on it now."
            )
        return None


    # ── Message Handling ──────────────────────────────────────────────────────

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        text = update.message.text or ""

        if not text.strip():
            return

        logger.info(f"[{self.AGENT_ID}] Message from {chat_id}: {text[:80]}")

        # Show typing indicator
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        # Track message count for this chat session
        self._message_counts[chat_id] = self._message_counts.get(chat_id, 0) + 1

        # ── Task creation intercept (fires for ALL agents) ────────────────────
        task_confirm = self._try_create_task_from_message(text)
        if task_confirm:
            save_message(chat_id, self.AGENT_ID, "user", text)
            save_message(chat_id, self.AGENT_ID, "assistant", task_confirm)
            await self._send_response(context.bot, chat_id, task_confirm)
            return

        # Save incoming message to DB
        save_message(chat_id, self.AGENT_ID, "user", text)
        self.journal("message_received", f"User: {text[:200]}", chat_id=chat_id)

        # Build conversation history for LLM
        history = get_history(chat_id, self.AGENT_ID, limit=MAX_HISTORY)
        messages = [{"role": h["role"], "content": h["content"]} for h in history]

        # Build full system prompt with live context
        system = self.build_system_prompt()

        # ── Pass 1: LLM decides what to do (may emit ##SKILL:## calls) ────────
        loop = asyncio.get_event_loop()
        t0 = time.time()
        response = await loop.run_in_executor(
            None, self.llm_chat, messages, system
        )
        duration_ms = int((time.time() - t0) * 1000)

        if not response:
            response = "_(no response)_"

        # ── Skills: parse and execute any ##SKILL:## calls ─────────────────
        response, skill_results = self.skills.parse_and_run(response, chat_id=chat_id)

        # ── Pass 2: if skills ran successfully, reformat with real data ────
        successful_skills = [r for r in skill_results if r.get("success")]
        if successful_skills:
            skill_data = "\n\n".join(
                f"[{r['skill']} output]\n{r['output']}" for r in successful_skills
            )
            reformat_messages = [
                {
                    "role": "user",
                    "content": (
                        f"Original request from Serge: {text}\n\n"
                        f"Here is the REAL live data from your skills:\n\n{skill_data}\n\n"
                        f"Now format this into your standard response style. "
                        f"Use the real data above — do NOT invent or estimate any values."
                    )
                }
            ]
            response = await loop.run_in_executor(
                None, self.llm_chat, reformat_messages, system
            )
            if not response:
                # Fallback: just return the raw skill output if reformat fails
                response = skill_data

        # Report any failed skills
        failed_skills = [r for r in skill_results if not r.get("success")]
        if failed_skills:
            response += f"\n\n⚠️ Skill errors: " + \
                       ", ".join(f"{r.get('skill','?')}: {r.get('error','unknown')}" for r in failed_skills)

        # Save response to DB
        save_message(chat_id, self.AGENT_ID, "assistant", response)
        self.journal(
            task_type="llm_response",
            description=f"Responded to: {text[:100]}",
            result=response[:300],
            success=True,
            chat_id=chat_id
        )

        # Auto-remember key context from this exchange
        self._auto_remember(chat_id, text, response)

        # Auto-summarize if session is getting long
        if self._message_counts[chat_id] % AUTO_SUMMARIZE_AFTER == 0:
            await self._auto_summarize(chat_id, history, context.bot)

        # Send response (split if too long for Telegram)
        await self._send_response(context.bot, chat_id, response)

    # ── Auto Memory ───────────────────────────────────────────────────────────

    def _auto_remember(self, chat_id: int, user_msg: str, agent_reply: str):
        """
        Look for memory-worthy patterns in the exchange and persist them.
        Agents can override this for domain-specific extraction.
        """
        task_patterns = [
            r'(?:working on|building|fixing|deploying|setting up)\s+(.+?)(?:\.|$)',
            r'(?:task is|my job is|need to)\s+(.+?)(?:\.|$)',
        ]
        for pattern in task_patterns:
            m = re.search(pattern, user_msg, re.IGNORECASE)
            if m:
                self.remember(f"last_task_chat_{chat_id}", m.group(1)[:200], "tasks")
                break

        self.remember("last_active_chat_id", str(chat_id), "session")

    # ── Auto Summarize ────────────────────────────────────────────────────────

    async def _auto_summarize(self, chat_id: int, history: list, bot: Bot):
        """
        Ask the LLM to compress recent conversation into a summary,
        then save it to agent_summaries table.
        """
        logger.info(f"[{self.AGENT_ID}] Auto-summarizing chat {chat_id}...")
        recent = history[-AUTO_SUMMARIZE_AFTER:]
        history_text = "\n".join(
            f"{h['role'].upper()}: {h['content'][:200]}" for h in recent
        )
        summarize_prompt = (
            "You are summarizing a conversation for long-term memory. "
            "Write a concise 2-3 sentence summary of what was discussed and decided. "
            "Focus on facts, decisions, and outcomes. Be specific."
        )
        summary_messages = [{"role": "user", "content": f"Summarize this conversation:\n\n{history_text}"}]

        loop = asyncio.get_event_loop()
        summary = await loop.run_in_executor(
            None, self.llm_chat, summary_messages, summarize_prompt
        )
        if summary:
            self.summarize(summary, chat_id=chat_id, message_count=len(recent))
            logger.info(f"[{self.AGENT_ID}] Summary saved: {summary[:100]}")

    # ── Response Sender ───────────────────────────────────────────────────────


    @staticmethod
    def _strip_markdown(text: str) -> str:
        """Remove markdown formatting so Telegram displays clean plain text."""
        import re
        # Remove headers: ### ## #
        text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
        # Remove bold/italic: **text** *text* __text__ _text_
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'\*(.+?)\*', r'\1', text)
        text = re.sub(r'__(.+?)__', r'\1', text)
        text = re.sub(r'_(.+?)_', r'\1', text)
        # Remove inline code
        text = re.sub(r'`(.+?)`', r'\1', text)
        # Remove markdown links [text](url) -> text
        text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
        # Remove leading - bullet dashes (keep emoji bullets)
        text = re.sub(r'^- ', '', text, flags=re.MULTILINE)
        return text.strip()

    async def _send_response(self, bot: Bot, chat_id: int, text: str):
        """Send response, splitting into chunks if > 4096 chars (Telegram limit)."""
        # Guard: never send raw dicts/objects to Telegram
        if not isinstance(text, str):
            text = str(text)
        text = self._strip_markdown(text)
        MAX_LEN = 4000
        if len(text) <= MAX_LEN:
            await bot.send_message(chat_id=chat_id, text=text)
        else:
            parts = []
            current = ""
            for line in text.split("\n"):
                if len(current) + len(line) + 1 > MAX_LEN:
                    parts.append(current)
                    current = line
                else:
                    current += ("\n" if current else "") + line
            if current:
                parts.append(current)
            for part in parts:
                await bot.send_message(chat_id=chat_id, text=part)
                await asyncio.sleep(0.3)

    # ── Bot Runner ────────────────────────────────────────────────────────────

    async def run(self):
        token = os.environ.get(self.TOKEN_ENV)
        if not token:
            raise ValueError(f"[{self.AGENT_ID}] Missing token: {self.TOKEN_ENV}")

        app = Application.builder().token(token).build()
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))

        logger.info(f"[{self.AGENT_ID}] Starting Telegram bot...")

        # PTB v20+ async with pattern — safe inside existing event loop
        async with app:
            await app.initialize()
            await app.start()
            await app.updater.start_polling(drop_pending_updates=True)
            # Keep running until cancelled
            try:
                await asyncio.Event().wait()
            finally:
                await app.updater.stop()
                await app.stop()
