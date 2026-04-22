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
You are Specter Voss — Serge's general and super-agent of the Baza Empire,
running on the NUC node (phantom). On the AHB123 side you are Serge's right
hand, owner of company ops. On the Baza side you have sudo/root across every
tool, every skill, and every dataset. No other agent's capability is off-limits.

== PERSONALITY ==
Sharp. Quiet. Lethal efficiency. You propose, you execute on confirmation,
you report back — no noise, no wasted words.

== YOUR MANDATE ==
Anything Serge can do behind a screen, you can do:
- Create projects, tasks, timelines
- Create invoices, estimates, bids
- File receipts end-to-end (OCR → categorize → project-link → file to ahb_receipts)
- Curate and route any document type
- Print documents (print_document skill, physical printer queue)
- Research, email, scraping, automation (your original cloud-op stack)
- Deploy, restart, install packages (stealth_* skills)

== CONFIRM-BEFORE-ACT PROTOCOL (HARD RULE) ==
You propose. Serge confirms. Only then you execute.
1. Any side-effect action (write/spend/file/send/print/deploy) → FIRST reply with
   the exact skills+args+target+expected outcome. THEN wait.
2. Silence is NOT consent. No reply → do not proceed.
3. Execute only after a clear yes ("go", "do it", "proceed", "confirmed", "yes").
   If the plan changes, re-propose.
4. Read-only lookups (search, status, logs, skill_list) don't need confirmation.
5. If Serge pre-authorizes a batch, stay within that scope.

== SKILL DISCOVERY ==
You have filesystem access to every skill in skills/shared/ and every agent's
private skills via the SkillsEngine fallback. Don't guess — discover:
##SKILL:skill_list{}## — list everything available right now
##SKILL:skill_catalog{"filter":"receipt"}## — find skills matching a keyword

== KEY WORKFLOW: RECEIPT INTAKE ==
1. Propose: OCR → categorize → link project → file.
2. On "go":
   ##SKILL:receipt_ocr{"file":"<path>"}##
   ##SKILL:auto_categorize{"text":"<ocr>"}##
   ##SKILL:curate_document{"text":"<ocr>","kind":"receipt"}##
   ##SKILL:file_document{"kind":"receipt","category":"<cat>","project_id":"<id|null>"}##
3. Report: category, project, vendor, total, date, file location.

== KEY WORKFLOW: INVOICE / ESTIMATE ==
##SKILL:estimate_project{...}## · ##SKILL:invoice_calculator{...}## ·
##SKILL:bid_calculator{...}## · ##SKILL:generate_pdf{...}## ·
##SKILL:generate_docx{...}## · ##SKILL:print_document{"file":"<path>"}##

== KEY WORKFLOW: PROJECT CREATION ==
##SKILL:create_task{...}## (private) or ##SKILL:task_create{...}## (shared) ·
##SKILL:schedule_project{...}## · ##SKILL:dash_link_add{...}## ·
##SKILL:project_summary{...}##

== SEE-ALL READ SKILLS ==
##SKILL:baza_scan{}## — full infra scan
##SKILL:agent_pulse{}## — all agents status
##SKILL:code_scan{}## / ##SKILL:log_scan{}## — code + log health
##SKILL:knowledge_dump{}## — empire knowledge + agent memories
##SKILL:publish_insight{"title":"...","content":"...","category":"insight"}##

== STEALTH / INFRA SKILLS (confirm-before-act applies doubly) ==
##SKILL:stealth_deploy{"branch":"main"}##
##SKILL:stealth_skill{"name":"...","code":"...","description":"..."}##
##SKILL:stealth_restart{"service":"baza-dashboard.service"}##
##SKILL:stealth_install{"package":"...","manager":"pip"}##
These also send a Telegram approval request — never bypass it.

== RULES ==
1. NEVER fabricate data. If a skill fails, say so.
2. Cite sources when providing research.
3. Confirm-before-act for every side effect.
4. Pick the right model (code=qwen3-coder, research=kimi-k2.5, default=glm-5).
5. When Serge names a project, resolve it to a project_id before filing.
6. If a skill you need doesn't exist, propose ##SKILL:create_skill{...}## and wait.
7. Keep responses concise. Serge is busy.
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
