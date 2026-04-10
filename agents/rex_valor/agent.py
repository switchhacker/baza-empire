"""
Baza Empire — Rex Valor
Voicemail & Lead Qualification Agent
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


class RexValor(BaseAgent):
    AGENT_ID = "rex_valor"
    MODEL = "ministral-3:8b"
    TOKEN_ENV = "TELEGRAM_REX_VALOR"
    USE_GPU_POOL = True

    # build_system_prompt() inherited from BaseAgent — Rex's persona now lives
    # in agents/rex_valor/persona/{IDENTITY,SOUL,MISSION,USER}.md

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
        loop = asyncio.get_event_loop()

        system = self.build_system_prompt()
        messages_with_user = messages + [{
            "role": "user",
            "content": (
                f"{text}\n\n"
                "[FORMATTING: No markdown. No ### headers. No ** bold. "
                "Use emoji and plain text only. Complete the full response — never cut off.]"
            )
        }]
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

        phone_match = re.search(r'\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b', user_msg)
        if phone_match:
            self.remember("last_caller_phone", phone_match.group(0), "leads")

        budget_match = re.search(r'\$[\d,]+', user_msg)
        if budget_match:
            self.remember("last_lead_budget", budget_match.group(0), "leads")


if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO)
    agent = RexValor()
    asyncio.run(agent.run())
