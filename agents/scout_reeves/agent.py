"""
Baza Empire — Scout Reeves
Research & Market Intelligence Agent (Super Mode)

Every inbound message goes through one pipeline:
  1. Acknowledge with a 1-line restatement of what Scout heard
  2. Parallel scrape: Baza FTS index + Web search (+ local-biz if location signal)
  3. Scrape top web URLs for body text
  4. LLM synthesis → structured Intel Report (Request / Baza / Web / How It Helps / Course of Action)
  5. Persist to empire_knowledge + publish research_complete event
"""
import re
import json as _json
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

MAX_HISTORY = 6

# Stopwords stripped from search-term extraction
STOPWORDS = {
    "a","an","the","and","or","but","of","for","to","in","on","at","by","with",
    "is","are","was","were","be","been","being","do","does","did","have","has",
    "had","i","me","my","we","us","our","you","your","he","she","it","they","them",
    "this","that","these","those","scout","please","can","could","would","should",
    "find","look","search","tell","show","get","go","up","out","about","into","from",
    "if","then","than","so","just","also","very","really","kind","sort","like",
    "what","who","where","when","why","how","need","want","make","made","let",
    "lets","let's","ok","okay","hey","hi","hello",
    "near","nearby","around","closest","local","area","here","there",
    "some","any","all","more","most","other","another","each","every",
    "good","best","top","new","old",
}

LOCAL_LOCATION_SIGNALS = [
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

    # ── classification helpers ─────────────────────────────────────────────

    def _extract_search_terms(self, text: str, n: int = 8) -> list:
        """Heuristic: drop stopwords/punct, preserve order, dedupe."""
        words = re.findall(r"[\w\-\.@/]+", text.lower())
        seen, out = set(), []
        for w in words:
            if len(w) < 2 or w in STOPWORDS:
                continue
            if w.isdigit() and len(w) < 3:
                continue
            if w in seen:
                continue
            seen.add(w)
            out.append(w)
            if len(out) >= n:
                break
        return out

    def _detect_location(self, text: str) -> tuple:
        """Return (has_location, zip_or_None)."""
        t = text.lower()
        zip_m = re.search(r"\b(\d{5})\b", t)
        if any(kw in t for kw in LOCAL_LOCATION_SIGNALS):
            return True, (zip_m.group(1) if zip_m else "19020")
        if re.search(r"\b(?:near|around|in)\s+\d{5}\b", t):
            return True, zip_m.group(1)
        return False, None

    def _describe_request(self, text: str, terms: list, has_loc: bool, zipc: str) -> str:
        """One-line plain-English restatement for the ack."""
        snippet = text.strip()
        if len(snippet) > 140:
            snippet = snippet[:137] + "..."
        topic = " ".join(terms[:5]) if terms else "(general)"
        loc = f" — local @ {zipc}" if has_loc and zipc else ""
        return f"🎯 SCOUT — Request received\n\"{snippet}\"\n📌 Topic: {topic}{loc}\n⏳ Super Mode: scraping Baza + web (~45s)..."

    # ── scrape stages (run inside executor) ────────────────────────────────

    def _baza_scrape(self, terms: list, limit: int = 6) -> list:
        """FTS over baza_knowledge_fts. Returns list of hits."""
        if not terms:
            return []
        q = " ".join(terms[:6])
        try:
            r = self.skills.run("knowledge_search", {"query": q, "limit": limit})
            # Skill exits 1 on no-results too, so accept output either way
            out = r.get("output") or ""
            if not out:
                return []
            data = _json.loads(out) if isinstance(out, str) else out
            if isinstance(data, dict) and data.get("ok"):
                return data.get("hits", []) or []
            return []
        except Exception as e:
            logger.warning(f"[scout_reeves] baza_scrape error: {e}")
            return []

    def _web_search(self, query: str, n: int = 6) -> list:
        try:
            r = self.skills.run("web_search", {"query": query[:200], "n": n, "output": "json"})
            if not r.get("success"):
                return []
            out = r.get("output", "")
            data = _json.loads(out) if isinstance(out, str) else out
            return data.get("results", []) if isinstance(data, dict) else []
        except Exception as e:
            logger.warning(f"[scout_reeves] web_search error: {e}")
            return []

    def _scrape_url(self, url: str, max_chars: int = 1800) -> str:
        try:
            r = self.skills.run("scrape_page", {"url": url, "max_chars": max_chars})
            return r.get("output", "") if r.get("success") else ""
        except Exception as e:
            logger.warning(f"[scout_reeves] scrape_page error {url}: {e}")
            return ""

    def _local_biz(self, query: str, zipc: str, n: int = 5) -> str:
        try:
            r = self.skills.run("local_business_search", {"query": query, "zip": zipc, "n": n})
            return r.get("output", "") if r.get("success") else ""
        except Exception as e:
            logger.warning(f"[scout_reeves] local_business_search error: {e}")
            return ""

    # ── formatting helpers ─────────────────────────────────────────────────

    def _format_baza(self, hits: list) -> str:
        if not hits:
            return "(none)"
        lines = []
        for h in hits[:6]:
            src = h.get("source", "?")
            title = (h.get("title") or "").strip() or str(h.get("source_id", "?"))
            body = (h.get("snippet") or h.get("body_snippet") or "").strip().replace("\n", " ")
            if len(body) > 200:
                body = body[:200] + "..."
            lines.append(f"- [{src}] {title}\n    {body}")
        return "\n".join(lines)

    def _format_web(self, results: list, scrapes: dict) -> str:
        if not results:
            return "(none)"
        lines = []
        for i, r in enumerate(results[:6], 1):
            title = (r.get("title") or "").strip()
            url = r.get("url") or ""
            snippet = (r.get("snippet") or "").strip().replace("\n", " ")
            if len(snippet) > 220:
                snippet = snippet[:220] + "..."
            lines.append(f"{i}. {title}\n    URL: {url}\n    SNIPPET: {snippet}")
            body = scrapes.get(url, "").strip()
            if body:
                body = body.replace("\n", " ")
                if len(body) > 800:
                    body = body[:800] + "..."
                lines.append(f"    BODY: {body}")
        return "\n".join(lines)

    # ── the pipeline ───────────────────────────────────────────────────────

    async def _super_mode(self, text: str, history: list) -> str:
        loop = asyncio.get_event_loop()

        terms = self._extract_search_terms(text)
        has_loc, zipc = self._detect_location(text)

        # Parallel: baza FTS + web search + (optional) local biz
        baza_task = loop.run_in_executor(None, self._baza_scrape, terms, 6)
        web_task = loop.run_in_executor(None, self._web_search, text, 6)
        biz_task = None
        if has_loc:
            # Strip filler words for a tight biz query
            biz_q = re.sub(
                r"\b(?:find me a|find me an|find a|find an|find me|find|search for|"
                r"look for|near me|near|local|in my area|recommend|closest|nearby|"
                r"who does|who can|where can i|research|search|please|scout)\b",
                " ", text, flags=re.IGNORECASE
            )
            biz_q = re.sub(r"\b\d{5}\b", "", biz_q)
            biz_q = re.sub(r"\s+", " ", biz_q).strip() or (" ".join(terms[:3]) or text[:60])
            biz_task = loop.run_in_executor(None, self._local_biz, biz_q, zipc, 5)

        baza_hits = await baza_task
        web_results = await web_task
        biz_block = await biz_task if biz_task else ""

        # Scrape top 2 web URLs in parallel for body context
        scrape_urls = [r.get("url") for r in web_results[:2] if r.get("url")]
        scrape_results = await asyncio.gather(*[
            loop.run_in_executor(None, self._scrape_url, u, 1800) for u in scrape_urls
        ]) if scrape_urls else []
        scrapes = dict(zip(scrape_urls, scrape_results))

        baza_block = self._format_baza(baza_hits)
        web_block = self._format_web(web_results, scrapes)
        local_block = biz_block.strip() if biz_block else ""

        # Synthesis prompt — inject findings as authoritative data
        system = self.build_system_prompt() or ""
        system += (
            "\n\n== SUPER MODE — INTEL SYNTHESIS ==\n"
            "You have just been handed fresh findings. Produce ONE structured intel report.\n"
            "Use this EXACT layout (plain text + emoji, no markdown headers, no ** bold):\n\n"
            "━━━━━━━━━━━━━━━━\n"
            "🔍 SCOUT INTEL — <short topic>\n"
            "━━━━━━━━━━━━━━━━\n\n"
            "📥 YOUR REQUEST\n"
            "<one-line plain restatement of what Serge asked, no quotes>\n\n"
            "🏛️ BAZA FINDINGS  (from local empire data)\n"
            "• <fact with [source] tag>  — if none, write: • No matching baza records.\n\n"
            "🌐 WEB FINDINGS\n"
            "• <title> — <url>\n"
            "  <key fact in one sentence>\n"
            "  (3–5 bullets max, each with the URL)\n\n"
            "📍 LOCAL  (omit this whole section if no local results)\n"
            "• <name> — <phone> — <address>\n\n"
            "🎯 HOW THIS HELPS\n"
            "👤 Serge: <concrete use for the user>\n"
            "🤖 Agents: <which agent can act — Phil/Sam/Rex/Duke/Nova/Claw/Simon/Specter — and how>\n"
            "🏗️ Baza / Infra: <what to wire up, change, or add inside the empire>\n\n"
            "💡 COURSE OF ACTION\n"
            "1. <concrete step>\n"
            "2. <step>\n"
            "3. <step>\n\n"
            "⚠️ WATCH: <single line — risk or thing to monitor; omit line if none>\n\n"
            "━━━━━━━━━━━━━━━━\n"
            "End with the literal token TASK_COMPLETE on its own line.\n"
            "Rules: cite URLs verbatim from the findings; do NOT invent sources; if findings are thin, "
            "say so honestly and recommend a tighter follow-up query.\n"
            "== END SUPER MODE ==\n"
        )

        findings_doc = (
            f"USER REQUEST:\n{text}\n\n"
            f"BAZA FTS HITS:\n{baza_block}\n\n"
            f"WEB RESULTS (with scraped bodies for top 2):\n{web_block}\n"
        )
        if local_block:
            findings_doc += f"\nLOCAL BUSINESS RESULTS:\n{local_block}\n"

        messages = list(history) + [{
            "role": "user",
            "content": findings_doc + "\n[Now write the Intel Report.]"
        }]

        response = await loop.run_in_executor(None, self.llm_chat, messages, system)
        return response or "_(no response)_"

    # ── telegram handler ───────────────────────────────────────────────────

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        text = (update.message.text or "").strip()
        if not text:
            return

        logger.info(f"[{self.AGENT_ID}] Message from {chat_id}: {text[:80]}")
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        save_message(chat_id, self.AGENT_ID, "user", text)
        self.journal("message_received", f"User: {text[:200]}", chat_id=chat_id)

        # Step 1 — describe + ack immediately so Serge sees Scout understood
        terms = self._extract_search_terms(text)
        has_loc, zipc = self._detect_location(text)
        ack = self._describe_request(text, terms, has_loc, zipc)
        await context.bot.send_message(chat_id=chat_id, text=ack)

        # Step 2 — pipeline
        history = get_history(chat_id, self.AGENT_ID, limit=MAX_HISTORY)
        messages = [{"role": h["role"], "content": h["content"]} for h in history]

        try:
            response = await self._super_mode(text, messages)
        except Exception as e:
            logger.exception(f"[scout_reeves] super_mode failed: {e}")
            response = (
                "━━━━━━━━━━━━━━━━\n"
                "🔍 SCOUT INTEL — error\n"
                "━━━━━━━━━━━━━━━━\n"
                "📥 YOUR REQUEST\n"
                f"{text[:200]}\n\n"
                f"⚠️ Super Mode pipeline failed: {e}\n"
                "Try again, or narrow the query.\n"
                "TASK_COMPLETE"
            )

        save_message(chat_id, self.AGENT_ID, "assistant", response)
        self.journal(
            task_type="llm_response",
            description=f"Super Mode report for: {text[:100]}",
            result=response[:300], success=True, chat_id=chat_id,
        )
        self._auto_remember(chat_id, text, response)
        await self._send_response(context.bot, chat_id, response)

    # ── persistence ────────────────────────────────────────────────────────

    def _auto_remember(self, chat_id: int, user_msg: str, agent_reply: str):
        super()._auto_remember(chat_id, user_msg, agent_reply)
        # Topic = first 5 non-stopword terms
        terms = self._extract_search_terms(user_msg)
        if not terms:
            return
        topic = " ".join(terms[:5])
        self.remember("last_research_topic", topic, "research")
        self._publish_research(topic, agent_reply)

    def _publish_research(self, topic: str, response: str):
        try:
            topic_slug = re.sub(r"[^\w]", "_", topic.lower())[:50].strip("_")
            empire_set(
                key=f"scout_intel_{topic_slug}",
                value=response[:1500],
                category="research",
                updated_by="scout_reeves",
            )
            publish_sync("scout_reeves", "research_complete", {
                "topic": topic_slug,
                "summary": response[:300],
                "artifact": "",
            })
        except Exception as e:
            logger.error(f"[scout_reeves] publish_research failed: {e}")

    async def _watch_for_research_needs(self):
        """Watch the task bus for items that look researchable and signal interest."""
        if not self.event_bus:
            return
        try:
            async for event in self.event_bus.listen("task_created"):
                title = event.data.get("title", "")
                desc = event.data.get("description", "")
                kw = ["research", "find", "competitor", "permit", "license",
                      "pricing", "market", "website", "squarespace", "compare"]
                if any(k in (title + " " + desc).lower() for k in kw):
                    logger.info(f"[scout_reeves] Auto-flagging research need: {title}")
                    await self.event_bus.publish("research_complete", {
                        "triggered_by": event.data.get("task_id"),
                        "topic": title,
                        "status": "queued",
                    })
        except Exception as e:
            logger.error(f"[scout_reeves] research watcher error: {e}")

    # ── run loop ───────────────────────────────────────────────────────────

    async def run(self):
        token = __import__("os").environ.get(self.TOKEN_ENV)
        if not token:
            raise ValueError(f"[{self.AGENT_ID}] Missing token: {self.TOKEN_ENV}")

        from telegram.ext import Application, MessageHandler, filters
        app = Application.builder().token(token).build()
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))

        logger.info(f"[{self.AGENT_ID}] Starting Telegram bot (Super Mode)...")

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
