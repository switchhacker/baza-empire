import os
import asyncio
import logging
import json
import random
import httpx
import redis
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from core.gpu_pool import gpu_pool

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class BazaAgent:
    def __init__(self, agent_id: str, config: dict, global_config: dict):
        self.agent_id = agent_id
        self.config = config
        self.global_config = global_config
        self.name = config['name']
        self.model = config['model']
        self.system_prompt = config['system_prompt']
        self.token = os.environ.get(config['telegram_token_env'])

        # Redis for group chat coordination
        self.redis = redis.Redis(
            host=global_config['redis']['host'],
            port=global_config['redis']['port'],
            decode_responses=True
        )

    # ─── Ollama via GPU Pool ──────────────────────────────────────────────────

    async def query_ollama(self, messages: list) -> str:
        """
        Acquire a free GPU slot from the pool, run inference, release.
        Blocks if both GPUs are busy — guarantees sequential group responses.
        """
        loop = asyncio.get_event_loop()

        # GPU acquire + HTTP call run in a thread so async loop stays free
        def _run():
            slot = gpu_pool.acquire(self.agent_id, timeout=120.0)
            if slot is None:
                return "_(No GPU available right now — try again.)_"
            try:
                import requests
                payload = {
                    "model": self.model,
                    "messages": [{"role": "system", "content": self.system_prompt}] + messages,
                    "stream": False
                }
                resp = requests.post(
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

        # Direct name mention
        if self.name.lower().split()[0] in text_lower:
            return True

        keywords = {
            'brad_gant':    ['infrastructure', 'research', 'server', 'network',
                              'hardware', 'intel', 'data', 'technical', 'brad'],
            'simon_bately': ['business', 'client', 'invoice', 'marketing',
                              'website', 'customer', 'sales', 'revenue', 'payroll', 'simon'],
            'claw_batto':   ['code', 'build', 'deploy', 'linux', 'docker', 'git',
                              'bug', 'script', 'install', 'devops', 'python', 'javascript', 'claw'],
            'phil_hass':    ['legal', 'contract', 'compliance', 'tax', 'finance',
                              'liability', 'regulation', 'accounting', 'phil'],
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

    # ─── Message Handler ─────────────────────────────────────────────────────

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.text:
            return

        chat_id = str(update.message.chat_id)
        chat_type = update.message.chat.type
        is_group = chat_type in ['group', 'supergroup']
        text = update.message.text
        sender = update.message.from_user.first_name

        # Ignore other bots in groups
        if is_group and update.message.from_user.is_bot:
            return

        # Task already handled
        if is_group and self.is_task_already_complete(chat_id):
            return

        # Not relevant to this agent
        if is_group and not self.should_respond(text, is_group):
            return

        # Stagger group responses so GPU pool works naturally
        if is_group:
            await asyncio.sleep(random.uniform(0.3, 1.5))
            if self.is_task_already_complete(chat_id):
                return

        # Save incoming message (per-agent history)
        self.save_message(chat_id, "user", f"{sender}: {text}")
        history = self.get_chat_history(chat_id)

        # Show typing indicator
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        try:
            # Blocks on GPU pool — sequential if both GPUs busy
            response = await self.query_ollama(history)
            clean = response.replace("TASK_COMPLETE", "").strip()

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

    # ─── Start ────────────────────────────────────────────────────────────────

    def run(self):
        if not self.token:
            logger.error(f"No token for {self.name} (env: {self.config['telegram_token_env']})")
            return

        logger.info(f"Starting {self.name} on GPU pool (AMD + NVIDIA)...")
        app = Application.builder().token(self.token).build()
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        app.run_polling(drop_pending_updates=True)
