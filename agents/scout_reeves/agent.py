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
from core.context_db import empire_set
from core.event_bus import publish_sync

logger = logging.getLogger(__name__)

MAX_HISTORY = 10

RESEARCH_KEYWORDS = [
    "research", "find", "look up", "check", "investigate", "who is",
    "what is", "competitor", "supplier", "pricing", "permit", "code",
    "regulation", "market", "intel", "rate", "cost", "compare", "search"
]

# Strong locational signals — these MUST be present for the local-business fast-path.
# Generic words like "find" or "search" alone are NOT enough — Scout is a general
# research agent, not just a local business finder.
LOCAL_SEARCH_LOCATION_SIGNALS = [
    "near me", "near 19020", "near zip", "in my area", "nearby", "closest",
    "in bensalem", "in philly", "in philadelphia", "in king of prussia",
    "in doylestown", "in warrington", "in jenkintown",
    "contractor near", "shop near", "service near", "provider near",
    "near my", "near here", "around me", "around here",
]


class ScoutReeves(BaseAgent):
    AGENT_ID = "scout_reeves"
    MODEL = "gpt-oss:20b"
    TOKEN_ENV = "TELEGRAM_SCOUT_REEVES"
    USE_GPU_POOL = True

    # build_system_prompt() inherited from BaseAgent — Scout's persona now lives
    # in agents/scout_reeves/persona/{IDENTITY,SOUL,MISSION,USER}.md

    def _is_research_request(self, text: str) -> bool:
        t = text.lower()
        return any(kw in t for kw in RESEARCH_KEYWORDS)

    def _is_local_search_request(self, text: str) -> bool:
        """Only fire the local-business fast-path when there's a CLEAR locational
        signal (zip code, 'near me', explicit city). Generic 'find me X' is NOT
        enough — Scout is a general research agent, not a near-me-only bot."""
        t = text.lower()
        # Strong location-phrase signal
        if any(kw in t for kw in LOCAL_SEARCH_LOCATION_SIGNALS):
            return True
        # 5-digit zip preceded by "near" / "around" / "in"
        if re.search(r'\b(?:near|around|in)\s+\d{5}\b', t):
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
            topic = topic_match.group(1).strip()
            self.remember("last_research_topic", topic, "research")
            # Auto-publish research results to empire_knowledge
            self._publish_research(topic, agent_reply)

    def _publish_research(self, topic: str, response: str):
        """Publish completed research to empire_knowledge and event bus."""
        try:
            topic_slug = re.sub(r'[^\w]', '_', topic.lower())[:50].strip('_')
            empire_set(
                key=f"scout_research_{topic_slug}",
                value=response[:1000],
                category="research",
                updated_by="scout_reeves"
            )
            publish_sync("scout_reeves", "research_complete", {
                "topic": topic_slug,
                "summary": response[:300],
                "artifact": ""
            })
        except Exception as e:
            logger.error(f"[scout_reeves] Failed to publish research: {e}")

    async def _watch_for_research_needs(self):
        """Watch for new tasks that might need research."""
        if not self.event_bus:
            return
        try:
            async for event in self.event_bus.listen("task_created"):
                title = event.data.get("title", "")
                desc = event.data.get("description", "")
                research_keywords = [
                    "research", "find", "competitor", "permit", "license",
                    "pricing", "market", "website", "squarespace"
                ]
                needs_research = any(
                    kw in (title + " " + desc).lower() for kw in research_keywords
                )
                if needs_research:
                    logger.info(f"[scout_reeves] Auto-researching for task: {title}")
                    await self.event_bus.publish("research_complete", {
                        "triggered_by": event.data.get("task_id"),
                        "topic": title,
                        "status": "queued"
                    })
        except Exception as e:
            logger.error(f"[scout_reeves] Research watcher error: {e}")

    async def run(self):
        """Override run to also start the research watcher."""
        # Start the research watcher as a background task once the event loop is available
        self._research_watcher_started = False
        original_run = super().run

        # We need to hook into the run lifecycle; override to add our task
        token = __import__('os').environ.get(self.TOKEN_ENV)
        if not token:
            raise ValueError(f"[{self.AGENT_ID}] Missing token: {self.TOKEN_ENV}")

        from telegram.ext import Application, MessageHandler, filters
        app = Application.builder().token(token).build()
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))

        logger.info(f"[{self.AGENT_ID}] Starting Telegram bot...")

        async with app:
            await app.initialize()
            await app.start()
            asyncio.ensure_future(self._heartbeat_loop())
            asyncio.ensure_future(self._artifact_context_loop())
            asyncio.ensure_future(self.start_event_listener())
            asyncio.ensure_future(self._watch_for_research_needs())
            await app.updater.start_polling(drop_pending_updates=True)
            try:
                await asyncio.Event().wait()
            finally:
                await app.updater.stop()
                await app.stop()


if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO)
    agent = ScoutReeves()
    asyncio.run(agent.run())
