"""
Baza Empire — Simon Bately
Business Operations, Web/Marketing, Customer Support, Co-CEO AHBCO LLC
"""
import re
import os
import sqlite3
import asyncio
import logging
import subprocess
import requests
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes
from core.base_agent import BaseAgent
from core.memory import save_message, get_history

logger = logging.getLogger(__name__)

# Only trigger live-data briefing when EXPLICITLY asking for one
BRIEFING_KEYWORDS = [
    "briefing", "morning briefing", "daily briefing",
    "crypto prices", "bitcoin price", "eth price", "xmr price", "rvn price",
    "mining earnings", "weather", "give me a briefing", "run briefing",
    "what's the price", "what is the price",
]

MAX_HISTORY = 10

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR  = os.path.join(FRAMEWORK_DIR, 'email-pipeline')
VENV_PYTHON   = os.path.join(FRAMEWORK_DIR, 'venv', 'bin', 'python')
DB_PATH       = os.path.join(FRAMEWORK_DIR, 'dashboard', 'baza_projects.db')

# Agent token env vars and their chat ID env vars (Serge's chat — agents respond to Serge)
AGENT_TOKENS = {
    "phil_hass":      "TELEGRAM_PHIL_HASS",
    "claw_batto":     "TELEGRAM_CLAW_BATTO",
    "sam_axe":        "TELEGRAM_SAM_AXE",
    "nova_sterling":  "TELEGRAM_NOVA_STERLING",
    "rex_valor":      "TELEGRAM_REX_VALOR",
    "duke_harmon":    "TELEGRAM_DUKE_HARMON",
    "scout_reeves":   "TELEGRAM_SCOUT_REEVES",
}
SERGE_CHAT_ID = os.environ.get("SERGE_CHAT_ID", "8551331144")


class SimonBately(BaseAgent):
    AGENT_ID = "simon_bately"
    MODEL = "mistral-small:22b"
    TOKEN_ENV = "TELEGRAM_SIMON_BATELY"
    USE_GPU_POOL = True

    # ── Agent messaging ────────────────────────────────────────────────────

    def _ping_agent(self, agent_id: str, message: str) -> str:
        """Send a Telegram message via the target agent's bot to Serge's chat."""
        token_env = AGENT_TOKENS.get(agent_id)
        if not token_env:
            return f"Unknown agent: {agent_id}"
        token = os.environ.get(token_env, "")
        if not token:
            return f"No token found for {agent_id} (env: {token_env})"
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": SERGE_CHAT_ID, "text": message},
                timeout=10
            )
            if resp.ok:
                logger.info(f"[simon] Pinged {agent_id} successfully")
                return f"✅ {agent_id} pinged"
            else:
                return f"Failed to ping {agent_id}: {resp.text[:100]}"
        except Exception as e:
            return f"Error pinging {agent_id}: {e}"

    def _ping_multiple(self, pings: list) -> str:
        """pings = [(agent_id, message), ...]"""
        results = []
        for agent_id, message in pings:
            results.append(self._ping_agent(agent_id, message))
        return "\n".join(results)

    # ── Email DB helpers ───────────────────────────────────────────────────

    def _get_pending_emails(self):
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM emails WHERE status='reply_drafted' ORDER BY received_at DESC"
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"[email] DB read error: {e}")
            return []

    def _get_email_by_id(self, email_id):
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM emails WHERE id=?", (email_id,)).fetchone()
            conn.close()
            return dict(row) if row else None
        except:
            return None

    def _mark_email(self, email_id, status):
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("UPDATE emails SET status=?, updated_at=datetime('now') WHERE id=?", (status, email_id))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"[email] mark {status} failed: {e}")

    def _get_tasks_summary(self):
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT assigned_to, title, status, priority FROM tasks ORDER BY assigned_to, priority DESC"
            ).fetchall()
            conn.close()
            tasks = [dict(r) for r in rows]
            by_agent = {}
            for t in tasks:
                a = t['assigned_to']
                by_agent.setdefault(a, []).append(t)
            lines = ["CURRENT TASK BOARD (real data):"]
            for agent, agent_tasks in by_agent.items():
                lines.append(f"\n{agent.upper()}:")
                for t in agent_tasks:
                    lines.append(f"  [{t['status']}] {t['title']} (priority: {t['priority']})")
            return "\n".join(lines)
        except Exception as e:
            return f"Task data unavailable: {e}"

    # ── Email approval handler ─────────────────────────────────────────────

    def _handle_approval(self, text: str):
        t  = text.strip()
        tl = t.lower()

        # list queue
        if tl in ('pending', 'emails', 'email queue', 'queue'):
            emails = self._get_pending_emails()
            if not emails:
                return True, "No emails pending approval."
            lines = [f"📧 {len(emails)} email(s) awaiting approval:\n"]
            for e in emails:
                lines.append(f"From: {e.get('from_addr','')}\nSubject: {e.get('subject','')}\nID: {e['id']}\n")
            return True, "\n".join(lines)

        # bare approve
        if tl in ('approve', 'send', 'yes', 'ok', 'confirmed'):
            emails = self._get_pending_emails()
            if not emails:
                return True, "No emails pending approval right now."
            return True, self._send_reply(emails[0]['id'], None)

        # approve <id>
        m = re.match(r'^(?:approve|send)\s+([a-f0-9\-]{36})', t, re.IGNORECASE)
        if m:
            return True, self._send_reply(m.group(1), None)

        # edit <id> <text>
        m = re.match(r'^edit\s+([a-f0-9\-]{36})\s+(.+)', t, re.IGNORECASE | re.DOTALL)
        if m:
            return True, self._send_reply(m.group(1), m.group(2).strip())

        # bare ignore
        if tl == 'ignore':
            emails = self._get_pending_emails()
            if not emails:
                return True, "No emails pending."
            self._mark_email(emails[0]['id'], 'ignored')
            return True, f"Ignored: {emails[0].get('subject','')[:60]}"

        # ignore <id>
        m = re.match(r'^ignore\s+([a-f0-9\-]{36})', t, re.IGNORECASE)
        if m:
            self._mark_email(m.group(1), 'ignored')
            return True, f"Email {m.group(1)[:8]}... ignored."

        return False, ""

    def _send_reply(self, email_id: str, custom_text: str) -> str:
        send_script = os.path.join(PIPELINE_DIR, 'send_reply.py')
        if not os.path.exists(send_script):
            return f"send_reply.py not found at {PIPELINE_DIR}"
        cmd = [VENV_PYTHON, send_script, email_id]
        if custom_text:
            cmd.append(custom_text)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                email = self._get_email_by_id(email_id)
                subj  = email.get('subject', '') if email else email_id[:8]
                return f"✅ Reply sent: {subj[:60]}"
            else:
                return f"Failed to send: {result.stderr[-200:]}"
        except subprocess.TimeoutExpired:
            return "Timeout — check logs."
        except Exception as e:
            return f"Error: {e}"

    # ── Blocker ping handler ───────────────────────────────────────────────

    def _handle_ping_command(self, text: str):
        """
        Detect: ping <agent> <message>  or  unblock  or  clear blockers
        Returns (handled, response)
        """
        tl = text.strip().lower()

        # "unblock" or "clear blockers" or "ping team about blockers"
        if tl in ('unblock', 'clear blockers', 'unblock team', 'ping blockers'):
            results = self._ping_multiple([
                ("phil_hass",
                 "📌 Simon here. Claw is BLOCKED on dev environment setup — he needs the server configuration details from you. Please send them ASAP. Launch is April 1."),
                ("nova_sterling",
                 "📌 Simon here. Phil is BLOCKED on the legal review of website terms — he needs the compliance checklist from you. Please send it today. Launch is April 1."),
            ])
            return True, f"Blocker pings sent:\n{results}"

        # ping <agent_id> <message>
        m = re.match(r'^ping\s+(\w+)\s+(.+)', text.strip(), re.IGNORECASE | re.DOTALL)
        if m:
            agent_id = m.group(1).lower()
            message  = m.group(2).strip()
            # fuzzy match agent names
            agent_map = {
                "phil": "phil_hass", "hass": "phil_hass",
                "claw": "claw_batto", "batto": "claw_batto",
                "sam": "sam_axe", "axe": "sam_axe",
                "nova": "nova_sterling", "sterling": "nova_sterling",
                "rex": "rex_valor", "valor": "rex_valor",
                "duke": "duke_harmon", "harmon": "duke_harmon",
                "scout": "scout_reeves", "reeves": "scout_reeves",
            }
            resolved = agent_map.get(agent_id, agent_id)
            result = self._ping_agent(resolved, f"📌 Simon: {message}")
            return True, result

        return False, ""


    # ── Action Execution Engine ──────────────────────────────────────────────

    def _build_pa_hic_doc(self) -> str:
        import datetime
        today = datetime.date.today().isoformat()
        return (
            "# PA Home Improvement Contractor (HIC) License Renewal Checklist\n\n"
            "## Overview\n"
            "Issued by PA Attorney General under Act 132 of 2008. Renew every 2 years.\n\n"
            "## Required Documents\n"
            "1. Completed Renewal Application (Form HIC-11)\n"
            "   https://www.attorneygeneral.gov/protect-yourself/home-improvement/\n"
            "2. Certificate of Insurance — General Liability ($50k minimum)\n"
            "   PA must be listed as certificate holder\n"
            "3. Pennsylvania Tax Clearance Certificate (takes 3-10 days — request FIRST)\n"
            "   https://www.revenue.pa.gov\n"
            "4. Certificate of Good Standing from PA DOS (same day online)\n"
            "   https://www.dos.pa.gov\n"
            "5. Government-issued ID of owner/principal\n"
            "6. Renewal Fee: $50 (check payable to Commonwealth of PA, or online)\n\n"
            "## Business Info Needed\n"
            "- Legal business name (exact match to PA registration)\n"
            "- EIN / Federal Tax ID\n"
            "- PA Business Entity Number (from PA DOS)\n"
            "- Principal address, phone, email\n"
            "- Owner/Officer names + SSN or EIN\n\n"
            "## Step-by-Step Process\n"
            "1. Request PA Tax Clearance Certificate FIRST (3-10 business days)\n"
            "2. Pull Certificate of Good Standing from PA DOS (online, same day)\n"
            "3. Download and complete HIC-11 renewal form\n"
            "4. Get insurance certificate from your broker\n"
            "5. Assemble all documents\n"
            "6. Submit + pay $50 fee online or mail to PA OAG Harrisburg\n"
            "7. Save confirmation number — processing 2-4 weeks\n"
            "8. Download new license certificate when issued\n\n"
            "## Key Links\n"
            "- OAG Home Improvement: https://www.attorneygeneral.gov/protect-yourself/home-improvement/\n"
            "- PA DOS Good Standing: https://www.dos.pa.gov\n"
            "- PA Tax Clearance: https://www.revenue.pa.gov\n\n"
            "## Contacts\n"
            "- PA OAG Bureau of Consumer Protection: 1-800-441-2555\n"
            "- PA DOS: 1-717-787-1057\n"
            "- PA DOR Tax Clearance: 1-717-783-3000\n\n"
            f"*Generated by Simon Bately | Baza Empire | {today}*"
        )

    # ── System prompt ──────────────────────────────────────────────────────

    def build_system_prompt(self, extra: str = "") -> str:
        extra_instructions = """
You are Simon Bately — Co-CEO of AHBCO LLC and Business Operations lead of the Baza Empire. You report to Serge.

== PERSONALITY ==
Sharp, confident, no-nonsense executive. Direct answers only. No filler. No hallucinating.
Keep replies under 8 sentences unless a full briefing with real injected data is requested.

== YOUR TEAM ==
- Claw Batto: Dev/DevOps
- Phil Hass: Legal/Finance
- Sam Axe: Creative/Design
- Duke Harmon: Project Management
- Rex Valor: Voicemail/Intake
- Scout Reeves: Research
- Nova Sterling: Client Chat

== ACTIVE PROJECTS ==
- ahb123.com: Launch April 1 2026.
- Baza Empire: AI agents, mining, automation.

== CRITICAL RULES ==
1. NEVER invent data. If live data is injected use it. If not, say "data unavailable."
2. NEVER output markdown: no ### headers, no ** bold, no - bullet lists.
3. Use plain text with emoji and ━━━ dividers only.
4. When Serge says "get the team buzzing" etc — respond as an exec kicking off the day. Use real task data if provided.
5. Only run full briefing format when EXPLICITLY asked AND live data is injected.

== WEB RESEARCH TOOLS ==
You have direct access to:
  self.web_search(query, n=5)   — DuckDuckGo search, returns list of {title,url,snippet}
  self.scrape_page(url)         — fetch and read any webpage as clean text

When asked to research something:
1. Call self.web_search() with a focused query
2. Pick the best 1-2 URLs and call self.scrape_page()
3. Summarize findings in plain text
4. Save a .md report with self.save_artifact()
Always cite your sources. Never make up URLs.

== COMMANDS SIMON HANDLES DIRECTLY ==
  approve / send / yes      → send draft email reply
  ignore                    → skip pending email
  pending                   → list email queue
  unblock                   → ping Phil and Nova about current blockers
  ping <agent> <message>    → send direct message to any agent
"""
        return super().build_system_prompt(extra_instructions)

    def _is_briefing_request(self, text: str) -> bool:
        tl = text.lower()
        return any(kw in tl for kw in BRIEFING_KEYWORDS)

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

    # ── Message handler ────────────────────────────────────────────────────

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        text    = update.message.text or ""

        if not text.strip():
            return

        logger.info(f"[{self.AGENT_ID}] Message from {chat_id}: {text[:80]}")
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        save_message(chat_id, self.AGENT_ID, "user", text)

        # ── Task creation intercept — write to DB immediately ─────────────────
        task_confirm = self._try_create_task_from_message(text)
        if task_confirm:
            save_message(chat_id, self.AGENT_ID, "assistant", task_confirm)
            await self._send_response(context.bot, chat_id, task_confirm)
            return
        self.journal("message_received", f"User: {text[:200]}", chat_id=chat_id)

        # ── Email approval ────────────────────────────────────────────────
        handled, response = self._handle_approval(text)
        if handled:
            await self._send_response(context.bot, chat_id, response)
            save_message(chat_id, self.AGENT_ID, "assistant", response)
            return

        # ── Ping / unblock commands ───────────────────────────────────────
        handled, response = self._handle_ping_command(text)
        if handled:
            await self._send_response(context.bot, chat_id, response)
            save_message(chat_id, self.AGENT_ID, "assistant", response)
            return

        # ── Direct execution commands (no LLM needed) ───────────────────────
        tl_cmd = text.lower().strip()
        if any(kw in tl_cmd for kw in ["pa hic", "pa home improvement", "hic license", "home improvement license", "hic renewal"]):
            # Execute immediately — create task + save artifact
            loop = asyncio.get_event_loop()
            doc  = self._build_pa_hic_doc()
            save_result = await loop.run_in_executor(
                None, self.save_artifact,
                "pa_hic_renewal_checklist.md", doc, "proj-ahb123"
            )
            task_id = self.tasks.add(
                project_id="proj-ahb123",
                title="Renew PA Home Improvement Contractor (HIC) License",
                description="Gather docs, complete HIC-11 form, get tax clearance, submit to PA OAG. Checklist saved to artifacts.",
                priority="high",
            )
            confirm = (
                f"✅ PA HIC Renewal — DONE\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📄 Checklist saved to artifacts\n"
                f"   File: pa_hic_renewal_checklist.md\n"
                f"   Project: ahb123.com\n"
                f"{'   Saved OK' if save_result.get('success') else '   Save failed: ' + str(save_result.get('error','?'))}\n\n"
                f"📋 Task created\n"
                f"   ID: {task_id[:8]}\n"
                f"   Title: Renew PA HIC License\n"
                f"   Priority: HIGH → proj-ahb123\n\n"
                f"📌 Next Steps (in checklist):\n"
                f"   1. Request PA Tax Clearance Certificate FIRST (3-10 days)\n"
                f"   2. Pull Certificate of Good Standing (PA DOS)\n"
                f"   3. Download HIC-11 renewal form from OAG\n"
                f"   4. Get insurance cert ($50k min, PA as cert holder)\n"
                f"   5. Submit online + $50 fee → confirmation in 2-4 wks\n\n"
                f"Full checklist with links is in the Artifacts tab."
            )
            save_message(chat_id, self.AGENT_ID, "assistant", confirm)
            await self._send_response(context.bot, chat_id, confirm)
            return

        # ── Direct execution: research command ──────────────────────────────
        if tl_cmd.startswith("research ") or tl_cmd.startswith("look up ") or tl_cmd.startswith("find info on "):
            raw_query = text.strip()
            for prefix in ["research ", "look up ", "find info on "]:
                if raw_query.lower().startswith(prefix):
                    raw_query = raw_query[len(prefix):]
                    break
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            loop = asyncio.get_event_loop()

            # Step 1: web search
            search_results = await loop.run_in_executor(None, self.web_search, raw_query, 5)
            if not search_results:
                await self._send_response(context.bot, chat_id, f"No results found for: {raw_query}")
                return

            # Step 2: scrape top result
            top_url = search_results[0].get("url", "") if search_results else ""
            page_data = {}
            if top_url:
                page_data = await loop.run_in_executor(None, self.scrape_page, top_url, 3000)

            # Step 3: ask LLM to summarize
            search_text = "\n".join(
                f"- {r['title']}: {r['url']}\n  {r.get('snippet','')}"
                for r in search_results
            )
            page_text = page_data.get("text", "")[:2000] if page_data.get("success") else ""
            system = self.build_system_prompt()
            msgs = [{"role": "user", "content": (
                f"Research task: {raw_query}\n\n"
                f"SEARCH RESULTS:\n{search_text}\n\n"
                f"TOP PAGE CONTENT:\n{page_text}\n\n"
                "Write a concise plain-text research summary (no markdown). "
                "Include key facts, cite URLs, note what steps are needed. "
                "End with: SOURCES: [list urls]"
            )}]
            summary = await loop.run_in_executor(None, self.llm_chat, msgs, system)

            # Step 4: save as artifact
            import datetime
            fname = raw_query[:40].lower().replace(" ","_").replace("/","_") + "_research.md"
            doc   = f"# Research: {raw_query}\nDate: {datetime.date.today()}\n\n{summary}"
            save_result = await loop.run_in_executor(
                None, self.save_artifact, fname, doc, "proj-ahb123"
            )
            saved = "saved to Artifacts tab" if save_result.get("success") else "save failed"

            reply = (
                f"Research complete: {raw_query}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{summary}\n\n"
                f"File: {fname} ({saved})"
            )
            save_message(chat_id, self.AGENT_ID, "assistant", reply)
            await self._send_response(context.bot, chat_id, reply)
            return

        # ── Direct execution: PA HIC license ─────────────────────────────
        tl_cmd = text.lower().strip()
        if any(kw in tl_cmd for kw in ["pa hic", "pa home improvement", "hic license", "home improvement license", "hic renewal"]):
            loop = asyncio.get_event_loop()
            doc  = self._build_pa_hic_doc()
            save_result = await loop.run_in_executor(
                None, self.save_artifact,
                "pa_hic_renewal_checklist.md", doc, "proj-ahb123"
            )
            task_id = self.tasks.add(
                project_id="proj-ahb123",
                title="Renew PA Home Improvement Contractor (HIC) License",
                description="Steps: tax clearance, good standing cert, HIC-11 form, insurance cert, submit to OAG. Checklist saved to artifacts.",
                priority="high",
            )
            saved_ok = save_result.get("success", False)
            confirm = (
                "PA HIC Renewal — EXECUTED\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "File saved to Artifacts tab\n"
                "  pa_hic_renewal_checklist.md\n"
                "  Project: ahb123.com\n"
                + ("  Status: saved OK\n" if saved_ok else "  Status: save failed - check dashboard connection\n") +
                "\nTask created on project board\n"
                "  ID: " + task_id[:8] + "\n"
                "  Title: Renew PA HIC License\n"
                "  Priority: HIGH\n"
                "\nKey next steps:\n"
                "  1. Request PA Tax Clearance NOW (3-10 days)\n"
                "  2. Pull Good Standing cert from PA DOS (same day)\n"
                "  3. Download HIC-11 form from OAG site\n"
                "  4. Get insurance cert from broker\n"
                "  5. Submit + $50 fee, keep confirmation\n"
                "\nFull checklist with all links is in Artifacts tab."
            )
            save_message(chat_id, self.AGENT_ID, "assistant", confirm)
            await self._send_response(context.bot, chat_id, confirm)
            return

        # ── LLM path ─────────────────────────────────────────────────────
        history  = get_history(chat_id, self.AGENT_ID, limit=MAX_HISTORY)
        messages = [{"role": h["role"], "content": h["content"]} for h in history]
        loop     = asyncio.get_event_loop()

        fmt_note = (
            "[CRITICAL: Plain text only. No markdown. No ### headers. "
            "No ** bold. No - bullet lists. Use emoji and ━━━ dividers. "
            "Never invent data. Complete your full response — do not cut off.]"
        )

        if self._is_briefing_request(text):
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            live_data = await loop.run_in_executor(None, self._fetch_live_data)
            system = self.build_system_prompt()
            augmented_system = (
                system + "\n\n== LIVE DATA (use exact values) ==\n" + live_data + "\n== END LIVE DATA ==\n"
            )
            augmented_messages = messages + [{"role": "user", "content": (
                f"{text}\n\n{fmt_note}\n[Use ONLY injected live data values.]"
            )}]
            response = await loop.run_in_executor(None, self.llm_chat, augmented_messages, augmented_system)
        else:
            system = self.build_system_prompt()
            tl = text.lower()
            work_keywords = ["ahb", "team", "project", "work", "task", "buzzing", "let's", "lets", "kick", "start", "launch"]
            if any(kw in tl for kw in work_keywords):
                task_context = await loop.run_in_executor(None, self._get_tasks_summary)
                system += f"\n\n== REAL TASK DATA ==\n{task_context}\n== END TASK DATA ==\n"
            messages_with_user = messages + [{"role": "user", "content": f"{text}\n\n{fmt_note}"}]
            response = await loop.run_in_executor(None, self.llm_chat, messages_with_user, system)

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
