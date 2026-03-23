import os
import asyncio
import logging
import json
import random
import time
import requests as req
import redis
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from core.gpu_pool import gpu_pool

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SIMON_TOKEN_ENV = "TELEGRAM_SIMON_BATELY"


class BazaAgent:
    def __init__(self, agent_id: str, config: dict, global_config: dict):
        self.agent_id = agent_id
        self.config = config
        self.global_config = global_config
        self.name = config['name']
        self.model = config['model']
        self.system_prompt = config['system_prompt']
        self.token = os.environ.get(config['telegram_token_env'])
        self.is_simon = (agent_id == 'simon_bately')

        self.redis = redis.Redis(
            host=global_config['redis']['host'],
            port=global_config['redis']['port'],
            decode_responses=True
        )

        # Simon gets the commander module
        if self.is_simon:
            from core.commander import SimonCommander
            self.commander = None  # initialized after first message (need Serge's chat_id)

    # ─── Ollama via GPU Pool ──────────────────────────────────────────────────

    async def query_ollama(self, messages: list) -> str:
        loop = asyncio.get_event_loop()

        def _run():
            slot = gpu_pool.acquire(self.agent_id, timeout=120.0)
            if slot is None:
                return "_(No GPU available right now — try again.)_"
            try:
                payload = {
                    "model": self.model,
                    "messages": [{"role": "system", "content": self.system_prompt}] + messages,
                    "stream": False
                }
                resp = req.post(
                    f"{slot.url}/api/chat",
                    json=payload,
                    timeout=120
                )
                resp.raise_for_status()
                return resp.json()["message"]["content"]
            except Exception as e:
                return f"_(model error: {str(e)})_"
            finally:
                gpu_pool.release(slot)

        return await loop.run_in_executor(None, _run)

    # ─── Relevance ────────────────────────────────────────────────────────────

    def should_respond(self, text: str, is_group: bool) -> bool:
        if not is_group:
            return True

        text_lower = text.lower()

        if self.name.lower().split()[0] in text_lower:
            return True

        keywords = {
            'simon_bately': ['business', 'client', 'invoice', 'marketing',
                              'website', 'customer', 'sales', 'revenue', 'payroll', 'simon',
                              'strategy', 'proposal', 'project', 'lead', 'coordinate', 'plan',
                              'brief', 'schedule', 'meeting', 'report', 'summary'],
            'claw_batto':   ['code', 'build', 'deploy', 'linux', 'docker', 'git',
                              'bug', 'script', 'install', 'devops', 'python', 'javascript',
                              'claw', 'security', 'hack', 'server', 'database', 'api'],
            'phil_hass':    ['legal', 'contract', 'compliance', 'tax', 'finance',
                              'liability', 'regulation', 'accounting', 'phil',
                              'license', 'gdpr', 'lawsuit', 'irs'],
            'sam_axe':      ['analytics', 'dashboard', 'kpi', 'metrics',
                              'media', 'video', 'audio', 'podcast', 'sound',
                              'marketing', 'campaign', 'ad', 'brand', 'seo', 'social',
                              'design', 'visual', 'graphic', 'logo', 'image', 'photo',
                              'architecture', 'render', 'drawing', 'layout', 'mockup',
                              'ocr', 'creative', 'art', 'sam'],
        }

        return any(kw in text_lower for kw in keywords.get(self.agent_id, []))

    # ─── Redis History ────────────────────────────────────────────────────────

    def get_chat_history(self, chat_id: str, limit: int = 10) -> list:
        key = f"chat:{self.agent_id}:{chat_id}:history"
        history = self.redis.lrange(key, -limit, -1)
        return [json.loads(m) for m in history]

    def save_message(self, chat_id: str, role: str, content: str):
        key = f"chat:{self.agent_id}:{chat_id}:history"
        self.redis.rpush(key, json.dumps({"role": role, "content": content}))
        self.redis.ltrim(key, -50, -1)
        self.redis.expire(key, 86400)

    # ─── Group coordination ───────────────────────────────────────────────────

    def is_task_complete(self, response: str) -> bool:
        return "TASK_COMPLETE" in response.upper()

    def mark_task_complete(self, chat_id: str):
        self.redis.set(f"chat:{chat_id}:task_complete", "1", ex=300)

    def is_task_already_complete(self, chat_id: str) -> bool:
        return self.redis.exists(f"chat:{chat_id}:task_complete") == 1

    # ─── Simon: parse delegation instructions from LLM output ────────────────

    def parse_dispatch(self, response: str) -> dict:
        """
        Simon's LLM can include a dispatch block like:
        DISPATCH:claw_batto:Set up the new VPS with Docker and Nginx.
        DISPATCH:phil_hass:Review the new contractor agreement for compliance.
        DISPATCH:sam_axe:Design a logo concept for AHBCO LLC.
        Returns dict of {agent_id: instruction}
        """
        assignments = {}
        for line in response.splitlines():
            if line.startswith("DISPATCH:"):
                parts = line.split(":", 2)
                if len(parts) == 3:
                    agent_id = parts[1].strip()
                    instruction = parts[2].strip()
                    assignments[agent_id] = instruction
        return assignments

    def init_commander(self, serge_chat_id: str):
        from core.commander import SimonCommander
        if self.commander is None:
            self.commander = SimonCommander(
                redis_client=self.redis,
                serge_chat_id=serge_chat_id,
                simon_token=self.token
            )

    # ─── Message Handler ─────────────────────────────────────────────────────

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.text:
            return

        chat_id = str(update.message.chat_id)
        chat_type = update.message.chat.type
        is_group = chat_type in ['group', 'supergroup']
        text = update.message.text
        sender = update.message.from_user.first_name

        if is_group and update.message.from_user.is_bot:
            return

        if is_group and self.is_task_already_complete(chat_id):
            return

        if is_group and not self.should_respond(text, is_group):
            return

        # ── Non-Simon agents: register chat with Simon + handle REPORT ───────
        if not self.is_simon:
            # Store this chat_id so Simon can dispatch back
            self.redis.set(f"agent:{self.agent_id}:serge_chat_id", chat_id, ex=86400 * 30)

            # If this is a Simon dispatch, route through and send REPORT back
            if text.startswith("[TASK:") and "Simon says:" in text:
                await self._handle_dispatch(update, context, chat_id, text)
                return

        # ── Simon: init commander with Serge's chat_id ───────────────────────
        if self.is_simon and not is_group:
            self.init_commander(chat_id)

        if is_group:
            await asyncio.sleep(random.uniform(0.3, 1.5))
            if self.is_task_already_complete(chat_id):
                return

        self.save_message(chat_id, "user", f"{sender}: {text}")
        history = self.get_chat_history(chat_id)

        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        try:
            response = await self.query_ollama(history)
            clean = response.replace("TASK_COMPLETE", "").strip()

            # ── Simon: extract and execute any DISPATCH commands ─────────────
            if self.is_simon and self.commander:
                assignments = self.parse_dispatch(clean)
                if assignments:
                    job_id = f"job_{int(time.time())}"
                    # Strip DISPATCH lines from the visible response
                    visible = "\n".join(
                        l for l in clean.splitlines()
                        if not l.startswith("DISPATCH:")
                    ).strip()
                    # Notify Serge of the plan
                    dispatch_summary = "\n".join(
                        f"  → {aid.replace('_', ' ').title()}: {inst[:80]}..."
                        for aid, inst in assignments.items()
                    )
                    notify = f"{visible}\n\n<b>Dispatching team:</b>\n{dispatch_summary}"
                    await update.message.reply_text(
                        f"<b>{self.name}:</b> {notify}",
                        parse_mode="HTML"
                    )
                    self.save_message(chat_id, "assistant", f"{self.name}: {visible}")
                    self.commander.create_job(job_id, assignments)
                    if self.is_task_complete(response):
                        self.mark_task_complete(chat_id)
                    return

            if clean:
                await update.message.reply_text(
                    f"<b>{self.name}:</b> {clean}",
                    parse_mode="HTML"
                )
                self.save_message(chat_id, "assistant", f"{self.name}: {clean}")

            if self.is_task_complete(response):
                self.mark_task_complete(chat_id)

        except Exception as e:
            logger.error(f"[{self.name}] Error: {e}")
            if not is_group:
                await update.message.reply_text(f"Error: {str(e)}")

    # ─── Non-Simon agents: handle a Simon dispatch ───────────────────────────

    async def _handle_dispatch(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                                chat_id: str, text: str):
        """Process a task dispatched by Simon, then send REPORT back."""
        # Extract task_id and instruction
        try:
            task_line = text.split("\n")[0]  # [TASK:job_xxx:agent_id]
            task_id = task_line.split("[TASK:")[1].rstrip("]")
            instruction = text.split("Simon says:\n\n")[1].split("\n\nWhen complete")[0].strip()
        except Exception:
            instruction = text
            task_id = f"unknown_{int(time.time())}"

        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        messages = [{"role": "user", "content": f"Simon orders: {instruction}"}]
        response = await self.query_ollama(messages)
        clean = response.replace("TASK_COMPLETE", "").strip()

        # Send report back — Simon's commander will pick this up
        report_msg = f"REPORT:{task_id}:{clean}"

        # Find Simon's chat ID for this agent to reply to
        simon_token = os.environ.get(SIMON_TOKEN_ENV)
        simon_chat_id = self.redis.get(f"agent:simon_bately:serge_chat_id")

        if simon_token and simon_chat_id:
            req.post(
                f"https://api.telegram.org/bot{simon_token}/sendMessage",
                json={"chat_id": simon_chat_id, "text": report_msg},
                timeout=15
            )
            logger.info(f"[{self.name}] Report sent to Simon for task {task_id}")
        else:
            logger.warning(f"[{self.name}] Could not find Simon's chat to report back")

        # Also confirm to Serge directly
        await update.message.reply_text(
            f"<b>{self.name}:</b> Task received from Simon. Working on it now.",
            parse_mode="HTML"
        )

    # ─── Start ────────────────────────────────────────────────────────────────

    def run(self):
        if not self.token:
            logger.error(f"No token for {self.name} (env: {self.config['telegram_token_env']})")
            return

        logger.info(f"Starting {self.name}...")
        app = Application.builder().token(self.token).build()
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        app.run_polling(drop_pending_updates=True)
