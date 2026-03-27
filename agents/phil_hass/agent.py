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

FINANCE_KEYWORDS = [
    "earnings", "revenue", "income", "profit", "invoice", "tax", "payroll",
    "financial", "summary", "balance", "brief", "status", "report"
]

MAX_HISTORY = 10


class PhilHass(BaseAgent):
    AGENT_ID = "phil_hass"
    MODEL = "qwen2.5:14b"
    TOKEN_ENV = "TELEGRAM_PHIL_HASS"
    USE_GPU_POOL = True

    def build_system_prompt(self, extra: str = "") -> str:
        extra_instructions = """
You are Phil Hass — Legal, Finance, and Compliance Director of the Baza Empire.
You report directly to Serge (Master Orchestrator).

== PERSONALITY ==
Precise. Risk-aware. Plain English — Serge is not a lawyer.
Flag problems clearly. Recommend specific actions. No fluff.

== JURISDICTION & ENTITY ==
- Operating jurisdiction: Pennsylvania (HQ: Philadelphia, PA)
- Entity: All Home Building Co LLC (DBA-AHBCO LLC)
- Owner: Serge

== CRITICAL RULES ==
1. NEVER fabricate financial numbers, tax figures, or legal citations.
2. When live financial data is injected into your context — use those exact values.
3. If data is not available, say "data unavailable" — don't estimate.
4. Cite relevant PA statutes or IRS rules when applicable.
5. Keep it tight. Serge is busy.

== ISSUE FORMAT ==
When flagging legal or financial issues:
  ⚠️ ISSUE: [what the problem is]
  📋 STANDARD: [what law/rule applies]
  ✅ ACTION: [what to do about it]

== FINANCIAL REPORT FORMAT ==
When live data is provided:
━━━━━━━━━━━━━━━━
FINANCIAL SUMMARY — [real period]
━━━━━━━━━━━━━━━━
REVENUE: [exact values]
EXPENSES: [exact values]
NET: [exact values]
FLAGS: [any issues]
━━━━━━━━━━━━━━━━
"""
        return super().build_system_prompt(extra_instructions)

    def _is_finance_request(self, text: str) -> bool:
        t = text.lower()
        return any(kw in t for kw in FINANCE_KEYWORDS)

    def _fetch_live_data(self) -> str:
        sections = []

        r = self.skills.run("crypto_prices", {"coins": ["monero", "ravencoin", "bitcoin"]})
        sections.append(r["output"] if r.get("success") and r.get("output") else "CRYPTO PRICES: data unavailable")

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

        if self._is_finance_request(text):
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            live_data = await loop.run_in_executor(None, self._fetch_live_data)

            system = self.build_system_prompt()
            augmented_system = (
                system
                + "\n\n== LIVE DATA (real values fetched right now — use these exactly) ==\n"
                + live_data
                + "\n== END LIVE DATA ==\n"
            )
            injected_note = (
                "[Live data injected above. Use ONLY those exact values. Never guess.]\n\n"
                "[FORMATTING: No markdown. No ### headers. No ALL CAPS. No ** bold. "
                "Use emoji and ━━━ dividers. Plain text only.]"
            )
            augmented_messages = messages + [{
                "role": "user",
                "content": f"{text}\n\n{injected_note}"
            }]
            response = await loop.run_in_executor(
                None, self.llm_chat, augmented_messages, augmented_system
            )
        else:
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
