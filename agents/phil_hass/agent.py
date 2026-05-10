"""
Baza Empire — Phil Hass
Legal, Finance, Compliance
"""
import re
import asyncio
import logging
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes
from core.base_agent import BaseAgent
from core.memory import save_message, get_history

logger = logging.getLogger(__name__)

MAX_HISTORY = 10


class PhilHass(BaseAgent):
    AGENT_ID = "phil_hass"
    MODEL = "qwen2.5:14b"
    TOKEN_ENV = "TELEGRAM_PHIL_HASS"
    USE_GPU_POOL = True

    # Per-task routing — Phil burns the right model for the right question.
    # Local-only by default to keep cloud spend at zero. Switch any value to
    # a cloud model (e.g. "cloud/glm-5") and the rate-limit fallback (Graft 4)
    # will catch quota errors automatically.
    MODEL_ROUTING = {
        "code":     "qwen2.5:14b",
        "legal":    "qwen2.5:14b",
        "research": "qwen2.5:14b",
        "fast":     "qwen2.5:14b",
        "default":  "qwen2.5:14b",
    }

    # build_system_prompt() inherited from BaseAgent — Phil's persona now lives
    # in agents/phil_hass/persona/{IDENTITY,SOUL,MISSION,USER}.md and is loaded
    # by ContextMixin.get_system_prompt(). Edit those files to change Phil's
    # voice or knowledge.

    # DocPrep intent detection lives in BaseAgent now — Phil inherits it.

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        text = update.message.text or ""

        if not text.strip():
            return

        logger.info(f"[{self.AGENT_ID}] Message from {chat_id}: {text[:80]}")
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        save_message(chat_id, self.AGENT_ID, "user", text)
        self.journal("message_received", f"User: {text[:200]}", chat_id=chat_id)

        # ── DocPrep intent intercept (inherited from BaseAgent) ───────────────
        intent = self._detect_doc_intent(text)
        if intent:
            reply = await self._handle_doc_intent(intent, text, chat_id)
            if reply:
                save_message(chat_id, self.AGENT_ID, "assistant", reply)
                await self._send_response(context.bot, chat_id, reply)
                return

        history = get_history(chat_id, self.AGENT_ID, limit=MAX_HISTORY)
        messages = [{"role": h["role"], "content": h["content"]} for h in history]
        loop = asyncio.get_event_loop()

        system = self.build_system_prompt()
        fmt_note = (
            "[FORMATTING: No markdown. No ### headers. No ALL CAPS. No ** bold. "
            "Use emoji for structure, plain text, and ━━━ dividers. "
            "Complete the full response — never cut off.]"
        )
        messages_with_user = messages + [{"role": "user", "content": f"{text}\n\n{fmt_note}"}]
        response = await loop.run_in_executor(
            None, self.llm_chat, messages_with_user, system
        )

        if not response:
            response = "_(no response)_"

        save_message(chat_id, self.AGENT_ID, "assistant", response)
        self.journal(
            task_type="llm_response",
            description=f"Responded to: {text[:100]}",
            result=response[:300],
            success=True,
            chat_id=chat_id
        )
        self._auto_remember(chat_id, text, response)
        await self._send_response(context.bot, chat_id, response)

    def _auto_remember(self, chat_id: int, user_msg: str, agent_reply: str):
        super()._auto_remember(chat_id, user_msg, agent_reply)

        path_match = re.search(r'(/\S+\.(?:pdf|docx|txt))', user_msg)
        if path_match:
            self.remember("last_document_reviewed", path_match.group(1), "documents")

        amount_match = re.search(r'\$[\d,]+', user_msg)
        if amount_match:
            self.remember("last_amount_discussed", amount_match.group(0), "finance")


if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO)
    agent = PhilHass()
    asyncio.run(agent.run())
