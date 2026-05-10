"""
Baza Empire — Simon Bately
Business Operations, Web/Marketing, Customer Support, Co-CEO AHBCO LLC
"""
import re
import asyncio
import logging
import time
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes
from core.base_agent import BaseAgent
from core.memory import save_message, get_history

logger = logging.getLogger(__name__)

BRIEFING_KEYWORDS = [
    "brief", "briefing", "status", "update", "summary", "morning",
    "weather", "news", "everything", "reach"
]

MAX_HISTORY = 10


class SimonBately(BaseAgent):
    AGENT_ID = "simon_bately"
    MODEL = "ministral-3:14b"
    TOKEN_ENV = "TELEGRAM_SIMON_BATELY"
    USE_GPU_POOL = True

    # build_system_prompt() inherited from BaseAgent — Simon's persona now lives
    # in agents/simon_bately/persona/{IDENTITY,SOUL,MISSION,USER}.md

    def _is_briefing_request(self, text: str) -> bool:
        t = text.lower()
        return any(kw in t for kw in BRIEFING_KEYWORDS)

    def _fetch_live_data(self) -> str:
        sections = []

        r = self.skills.run("weather", {"location": "Philadelphia, PA"})
        sections.append(r["output"] if r.get("success") and r.get("output") else "WEATHER: data unavailable")

        r = self.skills.run("news", {"category": "business"})
        sections.append(r["output"] if r.get("success") and r.get("output") else "NEWS: data unavailable")

        return "\n\n".join(sections)

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

        if self._is_briefing_request(text):
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            live_data = await loop.run_in_executor(None, self._fetch_live_data)

            system = self.build_system_prompt()
            augmented_system = (
                system
                + "\n\n== LIVE DATA (real values fetched right now — use these exactly) ==\n"
                + live_data
                + "\n== END LIVE DATA ==\n"
            )
            augmented_messages = messages + [{
                "role": "user",
                "content": (
                    f"{text}\n\n"
                    "[Live data injected above. Use ONLY those exact values. "
                    "Do NOT use markdown. Use emoji structure only. "
                    "Complete the full response — do not cut off.]"
                )
            }]
            response = await loop.run_in_executor(
                None, self.llm_chat, augmented_messages, augmented_system
            )
        else:
            system = self.build_system_prompt()
            messages_with_user = messages + [{
                "role": "user",
                "content": (
                    f"{text}\n\n"
                    "[FORMATTING: No markdown. No ### headers. No ** bold. "
                    "Use emoji for structure. Complete the full response — never cut off.]"
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

        client_match = re.search(r'client[:\s]+([A-Z][a-z]+ [A-Z][a-z]+)', user_msg)
        if client_match:
            self.remember("last_client_discussed", client_match.group(1), "clients")

        proj_match = re.search(r'project[:\s]+([^\.\,\n]+)', user_msg, re.IGNORECASE)
        if proj_match:
            self.remember("last_project_discussed", proj_match.group(1).strip()[:100], "projects")


if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO)
    agent = SimonBately()
    asyncio.run(agent.run())
