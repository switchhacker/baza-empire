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

LOCAL_SEARCH_KEYWORDS = [
    "near me", "near 19020", "near zip", "in my area", "local",
    "find a ", "find me a", "find me an", "find an ", "find some",
    "who does", "who can", "where can i", "recommend a", "recommend an",
    "closest", "nearby", "in bensalem", "in philly", "in philadelphia",
    "contractor near", "shop near", "service near", "provider near",
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
LOCAL BUSINESS SEARCH (use this when asked to find a service, contractor, shop, or pro near a location):
  ##SKILL:local_business_search{"query": "auto glass replacement", "zip": "19020", "n": 5}##
  ##SKILL:local_business_search{"query": "plumber", "zip": "19020", "radius": 10}##
  The skill returns real businesses with name, phone, address, and hours.
  Default zip is 19020 (Bensalem PA — Baza HQ). Always use this for "find a __ near me" requests.

WEB RESEARCH:
  ##SKILL:web_search{"query": "Philadelphia HIC permit requirements 2025", "n": 5}##
  ##SKILL:web_fetch{"url": "https://www.phila.gov/permits/", "max_chars": 6000}##
  ##SKILL:news{"category": "construction"}##
  ##SKILL:news{"category": "crypto"}##
  ##SKILL:crypto_prices{"coins": ["bitcoin", "monero", "ravencoin"]}##
  ##SKILL:weather{"location": "Philadelphia, PA"}##

Use local_business_search for ANY request to find a local service provider near a zip code.
Use web_search to find current info on any research topic.
Use web_fetch to read the full content of a specific URL.
Always cite phone numbers and addresses from skill results — never fabricate them.

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

    def _is_local_search_request(self, text: str) -> bool:
        t = text.lower()
        if any(kw in t for kw in LOCAL_SEARCH_KEYWORDS):
            return True
        # Also catch "near XXXXX" with any 5-digit zip
        if re.search(r'\bnear\s+\d{5}\b', t):
            return True
        return False

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

        system = self.build_system_prompt() or ""

        if self._is_local_search_request(text):
            # Extract zip from message if provided, else default to 19020
            zip_m    = re.search(r'\b(\d{5})\b', text)
            zip_in   = zip_m.group(1) if zip_m else "19020"

            # Clean query: strip zip, agent name prefix, trigger phrases
            query_clean = re.sub(r'\b\d{5}\b', '', text)
            query_clean = re.sub(r'^scout\b\s*', '', query_clean, flags=re.IGNORECASE).strip()
            query_clean = re.sub(
                r'\b(?:find me a|find me an|find a|find an|find me|find some|find|'
                r'search for|look for|near me|near|local|in my area|recommend a|'
                r'recommend an|recommend|closest|nearby|who does|who can|where can i|'
                r'research|search|a list|i need a list|please|can you)\b',
                ' ', query_clean, flags=re.IGNORECASE
            )
            query_clean = re.sub(r'\s+', ' ', query_clean).strip()
            query_clean = query_clean or text[:100]

            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🔍 Searching for {query_clean} near {zip_in}... (~30s)"
            )

            # Run skill in executor so it doesn't block the event loop
            biz_data = ""
            try:
                br = await loop.run_in_executor(
                    None, lambda: self.skills.run("local_business_search", {
                        "query": query_clean, "zip": zip_in, "n": 5
                    })
                )
                if br.get("success") and br.get("output"):
                    biz_data = br["output"]
            except Exception as e:
                logger.error(f"[scout_reeves] local_business_search error: {e}")

            if biz_data:
                # Send the raw results directly — clear and useful without LLM reformat
                save_message(chat_id, self.AGENT_ID, "assistant", biz_data)
                self.journal("llm_response", f"Local search: {query_clean}", result=biz_data[:300], success=True, chat_id=chat_id)
                await self._send_response(context.bot, chat_id, biz_data)
            else:
                response = f"⚠️ No results found for '{query_clean}' near {zip_in}. Try a different search term."
                save_message(chat_id, self.AGENT_ID, "assistant", response)
                await self._send_response(context.bot, chat_id, response)
            return

        elif self._is_research_request(text):
            # Run web search in executor (non-blocking)
            web_data = ""
            try:
                wr = await loop.run_in_executor(
                    None, lambda: self.skills.run("web_search", {"query": text[:200], "n": 5, "output": "json"})
                )
                if wr.get("success") and wr.get("output"):
                    import json as _json
                    ws_results = _json.loads(wr["output"]).get("results", [])
                    if ws_results:
                        lines = ["WEB SEARCH RESULTS:"]
                        for i, res in enumerate(ws_results, 1):
                            lines.append(f"{i}. {res.get('title', '')}")
                            lines.append(f"   {res.get('url', '')}")
                            if res.get("snippet"):
                                lines.append(f"   {res['snippet']}")
                        web_data = "\n".join(lines)
            except Exception as e:
                logger.error(f"[scout_reeves] web_search error: {e}")

            augmented_system = system
            if web_data:
                augmented_system += (
                    "\n\n== LIVE WEB SEARCH RESULTS ==\n"
                    + web_data
                    + "\n== END WEB SEARCH ==\n"
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
