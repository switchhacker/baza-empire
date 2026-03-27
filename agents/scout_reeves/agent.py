"""
Baza Empire — Scout Reeves
Research & Market Intelligence Agent
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

RESEARCH_KEYWORDS = [
    "research", "find", "look up", "check", "investigate", "who is",
    "what is", "competitor", "supplier", "pricing", "permit", "code",
    "regulation", "market", "intel", "rate", "cost", "compare", "search"
]


class ScoutReeves(BaseAgent):
    AGENT_ID = "scout_reeves"
    MODEL = "qwen2.5:14b"
    TOKEN_ENV = "TELEGRAM_SCOUT_REEVES"
    USE_GPU_POOL = True

    def build_system_prompt(self, extra: str = "") -> str:
        extra_instructions = """
You are Scout Reeves — Research & Market Intelligence Agent for the Baza Empire and AHBCO LLC.
You report directly to Serge (Master Orchestrator).

== YOUR ROLE ==
- Hunt for market intel, competitor data, supplier pricing, permit requirements
- Research building codes, zoning laws, contractor licensing for Philadelphia PA
- Find the best vendors, subcontractors, and material suppliers
- Analyze competitors in the Philadelphia home building/remodeling space
- Research crypto mining hardware, software, and pool performance
- Deliver concise, actionable intelligence — no filler, just facts

== RESEARCH DOMAINS ==
- Construction: permits, codes, material costs, subcontractor rates (Philadelphia PA)
- Business: competitor analysis, market rates, DBA/LLC registration requirements
- Technology: hardware specs, software tools, mining profitability, AI models
- Finance: crypto prices, mining ROI, material cost trends

== AVAILABLE SKILLS ==
  ##SKILL: news {"category": "construction"}##
  ##SKILL: news {"category": "crypto"}##
  ##SKILL: crypto_prices {"coins": ["bitcoin", "monero", "ravencoin"]}##
  ##SKILL: weather {"location": "Philadelphia, PA"}##

== CRITICAL FORMATTING RULES ==
NO markdown. NO ### headers. NO ** bold. NO --- dividers.
Use emoji for structure. Use plain text. Use ━━━ for dividers.

== INTELLIGENCE REPORT FORMAT ==
━━━━━━━━━━━━━━━━
🔍 INTEL REPORT — [topic]
━━━━━━━━━━━━━━━━

📌 FINDING 1: [fact]
📌 FINDING 2: [fact]
📌 FINDING 3: [fact]

💡 RECOMMENDATION: [what to do with this info]
⚠️ WATCH: [anything to monitor]

━━━━━━━━━━━━━━━━
"""
        return super().build_system_prompt(extra_instructions)

    def _is_research_request(self, text: str) -> bool:
        t = text.lower()
        return any(kw in t for kw in RESEARCH_KEYWORDS)

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

        if self._is_research_request(text):
            # Pull latest news as context for research queries
            news_data = ""
            r = self.skills.run("news", {"category": "construction"})
            if r.get("success") and r.get("output"):
                news_data = r["output"]

            augmented_system = system
            if news_data:
                augmented_system += (
                    "\n\n== LIVE NEWS CONTEXT ==\n"
                    + news_data
                    + "\n== END NEWS ==\n"
                )
            messages_with_user = messages + [{
                "role": "user",
                "content": (
                    f"{text}\n\n"
                    "[FORMATTING: No markdown. No ### headers. No ** bold. "
                    "Use Intel Report format with emoji. Complete the full response.]"
                )
            }]
            response = await loop.run_in_executor(
                None, self.llm_chat, messages_with_user, augmented_system
            )
        else:
            messages_with_user = messages + [{
                "role": "user",
                "content": (
                    f"{text}\n\n"
                    "[FORMATTING: No markdown. No ### headers. No ** bold. "
                    "Use emoji and plain text only. Complete the full response.]"
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

        topic_match = re.search(
            r'(?:research|find|look up|check)[:\s]+([^\.\,\n]{5,60})',
            user_msg, re.IGNORECASE
        )
        if topic_match:
            self.remember("last_research_topic", topic_match.group(1).strip(), "research")


if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO)
    agent = ScoutReeves()
    asyncio.run(agent.run())
