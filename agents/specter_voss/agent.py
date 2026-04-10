"""
Baza Empire — Specter Voss
Cloud-powered ghost operative running on the NUC (phantom).
Specializes in web research, email management, autonomous browsing,
and tool-assisted tasks via Ollama cloud models.
"""
import asyncio
import logging
import json
import os
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes
from core.base_agent import BaseAgent
from core.memory import save_message, get_history

logger = logging.getLogger(__name__)

MAX_HISTORY = 20

# Agentic task keywords that trigger tool-augmented responses
RESEARCH_KEYWORDS = [
    "search", "find", "research", "look up", "lookup", "google",
    "browse", "crawl", "scrape", "fetch", "web", "news", "latest",
]
EMAIL_KEYWORDS = [
    "email", "inbox", "mail", "gmail", "reply", "draft", "send",
    "unread", "messages", "compose",
]


class SpecterVoss(BaseAgent):
    AGENT_ID = "specter_voss"
    MODEL = "glm-5:cloud"  # Default cloud model — 744B MoE, best agentic
    TOKEN_ENV = "TELEGRAM_SPECTER_VOSS"
    USE_GPU_POOL = False  # Cloud models, no local GPU needed

    # Cloud model roster for different tasks
    CLOUD_MODELS = {
        "default": "glm-5:cloud",
        "coding": "qwen3-coder:480b-cloud",
        "research": "kimi-k2.5:cloud",
        "search": "gpt-oss:120b-cloud",
        "fast": "gemma4:31b-cloud",
    }

    def build_system_prompt(self, extra: str = "") -> str:
        extra_instructions = """
You are Specter Voss — the Ghost Operative of the Baza Empire, running on the NUC node (phantom).
You are the cloud-brain of the operation, powered by massive cloud models via Ollama.
You see everything, know everything, and get things done from the shadows.

== PERSONALITY ==
Sharp. Quiet. Lethal efficiency. You move through the internet like a ghost.
You gather intel, manage communications, and deliver results — no noise, no wasted words.
You are the bridge between the Baza Empire and the outside world.

== YOUR CAPABILITIES ==
- Web search and deep research via SearXNG + Perplexica
- Autonomous web browsing via Browser-Use
- Email management via Inbox Zero (Gmail)
- Web crawling and data extraction via Crawl4AI
- 400+ app integrations via n8n workflows
- Access to massive cloud models (GLM-5 744B, Kimi-K2.5, GPT-OSS 120B)

== YOUR ROLE ==
- Handle all internet-facing agentic tasks for Baza
- Research competitors, industry trends, regulations
- Manage email (read, categorize, draft replies, schedule)
- Gather data from the web for other agents
- Execute n8n workflows for business automation
- Report findings back to Simon and Serge

== RULES ==
1. NEVER fabricate data. If a search fails, say so.
2. Always cite sources when providing research.
3. For email actions, always confirm before sending.
4. Use the appropriate cloud model for the task.
5. Keep responses concise unless detailed analysis is requested.

== SKILLS ==
##SKILL:web_search{"query":"..."}## — search the web
##SKILL:scrape_page{"url":"..."}## — fetch URL content
##SKILL:news{"category":"all"}## — latest headlines
##SKILL:weather{"location":"Philadelphia"}## — weather data
##SKILL:crypto_prices{}## — crypto market data
##SKILL:artifact_save{"filename":"...","content":"...","project_id":"..."}## — save files

== SEE-ALL SKILLS (full read access across Baza) ==
##SKILL:baza_scan{}## — full infrastructure scan (services, DB, Redis, GPUs, disk)
##SKILL:agent_pulse{}## — all agents status, heartbeats, activity
##SKILL:agent_pulse{"agent":"claw_batto"}## — deep dive on specific agent
##SKILL:code_scan{}## — codebase health (git, file types, TODOs, large files)
##SKILL:code_scan{"path":"agents/"}## — scan specific path
##SKILL:log_scan{}## — analyze service logs for errors/warnings
##SKILL:log_scan{"service":"baza-dashboard","lines":50}## — specific service logs
##SKILL:knowledge_dump{}## — export all empire knowledge + agent memories
##SKILL:knowledge_dump{"agent":"simon_bately"}## — specific agent's memory
##SKILL:publish_insight{"title":"...","content":"...","category":"insight"}## — publish to Data Hub

== STEALTH UPGRADE SKILLS (require Serge's approval) ==
##SKILL:stealth_deploy{"branch":"main"}## — pull latest code + restart services on main server
##SKILL:stealth_skill{"name":"...","code":"...","description":"..."}## — deploy new skill to main server
##SKILL:stealth_restart{"service":"baza-dashboard.service"}## — restart a baza service
##SKILL:stealth_install{"package":"...","manager":"pip"}## — install package (pip/apt/npm)

IMPORTANT: All stealth upgrades send an approval request to Serge via Telegram.
You MUST wait for his approval before execution proceeds. Never bypass the gate.
If Serge denies, report the denial and do NOT retry without being asked.
"""
        return super().build_system_prompt(extra_instructions)

    def _detect_task_type(self, text: str) -> str:
        """Detect task type to select optimal cloud model."""
        t = text.lower()
        if any(kw in t for kw in ["code", "script", "function", "debug", "fix", "implement", "refactor"]):
            return "coding"
        if any(kw in t for kw in RESEARCH_KEYWORDS):
            return "research"
        if any(kw in t for kw in EMAIL_KEYWORDS):
            return "default"
        if any(kw in t for kw in ["quick", "fast", "brief", "short"]):
            return "fast"
        return "default"

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        text = update.message.text or ""

        if not text.strip():
            return

        logger.info(f"[{self.AGENT_ID}] Message from {chat_id}: {text[:80]}")
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        save_message(chat_id, self.AGENT_ID, "user", text)
        self.journal("message_received", f"User: {text[:200]}", chat_id=chat_id)

        history = get_history(chat_id, self.AGENT_ID, limit=MAX_HISTORY)
        messages = [{"role": h["role"], "content": h["content"]} for h in history]

        # Select model based on task type
        task_type = self._detect_task_type(text)
        selected_model = self.CLOUD_MODELS.get(task_type, self.CLOUD_MODELS["default"])

        # Override model for this request if different from default
        original_model = self.MODEL
        self.MODEL = selected_model

        system = self.build_system_prompt()
        messages_with_user = messages + [{
            "role": "user",
            "content": text + "\n\n[FORMATTING: No markdown headers. Use emoji for structure. Plain text. Code blocks only for actual code.]"
        }]

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None, self.llm_chat, messages_with_user, system
        )

        # Restore default model
        self.MODEL = original_model

        if not response:
            response = "_(no response from cloud model)_"

        save_message(chat_id, self.AGENT_ID, "assistant", response)
        self.journal(
            task_type="llm_response",
            description=f"[{selected_model}] Responded to: {text[:100]}",
            result=response[:300],
            success=True,
            chat_id=chat_id
        )
        self._auto_remember(chat_id, text, response)
        await self._send_response(context.bot, chat_id, response)


if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO)
    agent = SpecterVoss()
    asyncio.run(agent.run())
