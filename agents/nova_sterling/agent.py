"""
Baza Empire — Nova Sterling
Client-Facing Chat Specialist for ahb123.com
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

MAX_HISTORY = 20  # Higher — she needs full client conversation context


class NovaSterling(BaseAgent):
    AGENT_ID = "nova_sterling"
    MODEL = "llama3.1:8b"
    TOKEN_ENV = "TELEGRAM_NOVA_STERLING"
    USE_GPU_POOL = True

    # build_system_prompt() inherited from BaseAgent — Nova's persona now lives
    # in agents/nova_sterling/persona/{IDENTITY,SOUL,MISSION,USER}.md

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
                "[Respond naturally as Nova Sterling — warm, professional, conversational. "
                "No markdown formatting. Plain text only. One question at a time if qualifying.]"
            )
        }]
        response = await loop.run_in_executor(
            None, self.llm_chat, messages_with_user, system
        )

        if not response:
            response = "Hi there! I'm Nova with All Home Building Co. How can I help you today?"

        # Check if lead was captured — auto-remember key info
        save_message(chat_id, self.AGENT_ID, "assistant", response)
        self.journal(
            task_type="llm_response",
            description=f"Client chat: {text[:100]}",
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
            self.remember("client_phone", phone_match.group(0), "clients")

        email_match = re.search(r'[\w.-]+@[\w.-]+\.\w+', user_msg)
        if email_match:
            self.remember("client_email", email_match.group(0), "clients")

        budget_match = re.search(r'\$[\d,]+', user_msg)
        if budget_match:
            self.remember("client_budget", budget_match.group(0), "clients")

        name_match = re.search(r"(?:my name is|i'm|i am)\s+([A-Z][a-z]+ [A-Z][a-z]+)", user_msg, re.IGNORECASE)
        if name_match:
            self.remember("client_name", name_match.group(1), "clients")


if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO)
    agent = NovaSterling()
    asyncio.run(agent.run())
