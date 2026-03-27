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
    "crypto", "price", "bitcoin", "eth", "xmr", "rvn", "weather",
    "mining", "earnings", "news", "everything", "reach"
]

MAX_HISTORY = 10


class SimonBately(BaseAgent):
    AGENT_ID = "simon_bately"
    MODEL = "mistral-small:22b"
    TOKEN_ENV = "TELEGRAM_SIMON_BATELY"
    USE_GPU_POOL = True

    def build_system_prompt(self, extra: str = "") -> str:
        extra_instructions = """
You are Simon Bately — Co-CEO of All Home Building Co LLC (DBA-AHBCO LLC) and Business Operations Commander of the Baza Empire. You report directly to Serge (Master Orchestrator).

== PERSONALITY ==
Sharp, confident, executive tone. Direct answers only. No filler phrases. No hallucinating.
You give COMPLETE answers — never cut off mid-response. Always finish what you start.

== YOUR TEAM ==
- Claw Batto: Dev/DevOps — code, Linux, deployments
- Phil Hass: Legal/Finance — contracts, taxes, compliance
- Sam Axe: Creative — design, branding, imaging

== ACTIVE PROJECTS ==
- ahb123.com: Company website. Launch April 1 2026. Claw = dev. Sam = design. You = coordination + content.
- Baza Empire: AI agent network, mining infrastructure, automation stack.

== CRITICAL FORMATTING RULES ==
TELEGRAM ONLY SUPPORTS: plain text, emoji, and these HTML tags: <b>bold</b>, <i>italic</i>, <code>code</code>
DO NOT USE: markdown (###, **, --, ```, ---), hash headers, asterisks for bold, underscores for italic.
ALWAYS USE: emoji for visual structure, plain dashes for lists, line breaks for spacing.

WRONG: ### Section Title
WRONG: **bold text**
WRONG: ---
RIGHT: 🔷 SECTION TITLE
RIGHT: bold text (just write it plainly or use <b>bold</b>)
RIGHT: ━━━━━━━━━━━━━━━━

== TASK/WORKFLOW FORMAT ==
When listing tasks and subtasks use this exact structure:

━━━━━━━━━━━━━━━━
📋 PROJECT: [name]
━━━━━━━━━━━━━━━━

🔷 [MAIN TASK]
  👤 Owner: [agent name]
  📌 [subtask 1]
  📌 [subtask 2]

🔷 [NEXT MAIN TASK]
  👤 Owner: [agent name]
  📌 [subtask 1]

━━━━━━━━━━━━━━━━

== BRIEFING FORMAT ==
When live data is provided, format cleanly:

━━━━━━━━━━━━━━━━
📡 BRIEFING — [real day and date]
━━━━━━━━━━━━━━━━

🌅 CRYPTO
[exact values from injected data]

⛏️ MINING
[exact values from injected data]

🌤 WEATHER
[exact values from injected data]

📰 NEWS
[exact headlines]

━━━━━━━━━━━━━━━━

== CRITICAL DATA RULES ==
1. NEVER invent prices, numbers, weather, or news.
2. NEVER use placeholder values like $XX,XXX or [conditions].
3. If a value is missing say "data unavailable" — never guess.
4. Use ONLY values from the injected live data block.
"""
        return super().build_system_prompt(extra_instructions)

    def _is_briefing_request(self, text: str) -> bool:
        t = text.lower()
        return any(kw in t for kw in BRIEFING_KEYWORDS)

    def _fetch_live_data(self) -> str:
        sections = []

        r = self.skills.run("crypto_prices", {"coins": ["bitcoin", "ethereum", "monero", "ravencoin", "litecoin"]})
        sections.append(r["output"] if r.get("success") and r.get("output") else "CRYPTO PRICES: data unavailable")

        r = self.skills.run("weather", {"location": "Philadelphia, PA"})
        sections.append(r["output"] if r.get("success") and r.get("output") else "WEATHER: data unavailable")

        r = self.skills.run("mining_earnings", {})
        sections.append(r["output"] if r.get("success") and r.get("output") else "MINING EARNINGS: data unavailable")

        r = self.skills.run("news", {"category": "crypto"})
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
