import os
import re
import asyncio
import logging
import json
import random
import time
import requests as req
import httpx
import redis
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from core.gpu_pool import gpu_pool

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SIMON_TOKEN_ENV = "TELEGRAM_SIMON_BATELY"
TOOL_SERVER = "http://localhost:8000"

COMBINED_TRIGGERS = {
    'mining': ['mining', 'miner', 'mine', 'xmrig'],
    'crypto':  ['crypto', 'price', 'prices', 'coin', 'xmr', 'rvn', 'bitcoin', 'btc'],
    'disk':    ['disk', 'storage', 'space'],
    'docker':  ['docker', 'container'],
}


async def fire_tool(agent_slug: str, tool: str, input_data: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{TOOL_SERVER}/tools/{agent_slug}/{tool}",
                json={"input": input_data}
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.error(f"Tool {agent_slug}/{tool} failed: {e}")
        return {"success": False, "error": str(e)}


async def detect_and_fire_tools(text: str) -> dict:
    text_lower = text.lower()
    tasks = {}

    if any(kw in text_lower for kw in COMBINED_TRIGGERS['mining']):
        tasks['mining_status'] = fire_tool('claw', 'mining-status', {})
    if any(kw in text_lower for kw in COMBINED_TRIGGERS['crypto']):
        tasks['crypto_prices'] = fire_tool('sam', 'crypto-prices',
                                           {'coins': ['monero', 'ravencoin', 'bitcoin']})
    if any(kw in text_lower for kw in COMBINED_TRIGGERS['disk']):
        tasks['disk_usage'] = fire_tool('claw', 'disk-usage', {})
    if any(kw in text_lower for kw in COMBINED_TRIGGERS['docker']):
        tasks['docker_status'] = fire_tool('claw', 'docker-status', {})

    if not tasks:
        return {}

    results = {}
    tool_results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    for key, result in zip(tasks.keys(), tool_results):
        results[key] = result if not isinstance(result, Exception) else {"success": False, "error": str(result)}

    logger.info(f"Tools fired: {list(results.keys())}")
    return results


def format_tool_results(results: dict) -> str:
    if not results:
        return ""

    lines = ["[REAL-TIME DATA FROM BAZA SYSTEMS — USE THIS EXACT DATA, DO NOT MAKE UP NUMBERS]\n"]

    for key, result in results.items():
        if not result.get('success'):
            lines.append(f"{key}: ERROR — {result.get('error', 'unknown')}")
            continue

        output = result.get('output', {})

        if key == 'mining_status':
            lines.append(f"MINING STATUS: {json.dumps(output)}")

        elif key == 'crypto_prices':
            parts = []
            for coin, data in output.items():
                price = data.get('usd', 'N/A')
                change = data.get('usd_24h_change', 0)
                direction = '▲' if change >= 0 else '▼'
                parts.append(f"{coin.upper()}: ${price:,.2f} {direction}{abs(change):.1f}%")
            lines.append(f"LIVE CRYPTO PRICES: {' | '.join(parts)}")

        elif key == 'disk_usage':
            lines.append(f"DISK USAGE:\n{output.get('output', '')}")

        elif key == 'docker_status':
            containers = output.get('containers', [])
            if containers:
                c_list = ', '.join(c['name'] for c in containers)
                lines.append(f"DOCKER CONTAINERS ({output.get('count', 0)} running): {c_list}")
            else:
                lines.append("DOCKER CONTAINERS: none running")

    lines.append("\n[END REAL-TIME DATA — REPORT THESE EXACT NUMBERS TO SERGE]")
    return "\n".join(lines)


def strip_name_prefix(text: str, name: str) -> str:
    """Remove leading 'Name: ' or 'Name Surname: ' that the LLM adds to itself."""
    return re.sub(rf"^{re.escape(name)}:\s*", "", text, flags=re.IGNORECASE).strip()


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
        self.commander = None

        self.redis = redis.Redis(
            host=global_config['redis']['host'],
            port=global_config['redis']['port'],
            decode_responses=True
        )

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
                             'claw', 'security', 'server', 'database', 'api'],
            'phil_hass':    ['legal', 'contract', 'compliance', 'tax', 'finance',
                             'liability', 'regulation', 'accounting', 'phil',
                             'license', 'gdpr', 'irs'],
            'sam_axe':      ['analytics', 'dashboard', 'kpi', 'metrics',
                             'media', 'video', 'audio', 'campaign', 'brand', 'seo',
                             'design', 'visual', 'graphic', 'image', 'creative', 'sam'],
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

    # ─── Simon: parse DISPATCH lines ─────────────────────────────────────────

    def parse_dispatch(self, response: str) -> dict:
        assignments = {}
        for line in response.splitlines():
            if line.startswith("DISPATCH:"):
                parts = line.split(":", 2)
                if len(parts) == 3:
                    assignments[parts[1].strip()] = parts[2].strip()
        return assignments

    def init_commander(self, serge_chat_id: str):
        from core.commander import SimonCommander
        if self.commander is None:
            self.commander = SimonCommander(
                redis_client=self.redis,
                serge_chat_id=serge_chat_id,
                simon_token=self.token
            )

    # ─── Main message handler ─────────────────────────────────────────────────

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

        # ── Non-Simon: register chat ID + handle dispatches ───────────────────
        if not self.is_simon:
            self.redis.set(f"agent:{self.agent_id}:serge_chat_id", chat_id, ex=86400 * 30)
            if text.startswith("[TASK:") and "Simon says:" in text:
                await self._handle_dispatch(update, context, chat_id, text)
                return

        # ── Simon: init commander ─────────────────────────────────────────────
        if self.is_simon and not is_group:
            self.init_commander(chat_id)

        if is_group:
            await asyncio.sleep(random.uniform(0.3, 1.5))
            if self.is_task_already_complete(chat_id):
                return

        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        # ── Simon: fire tools BEFORE querying LLM ────────────────────────────
        tool_context = ""
        if self.is_simon:
            tool_results = await detect_and_fire_tools(text)
            if tool_results:
                tool_context = format_tool_results(tool_results)

        user_message = f"{text}\n\n{tool_context}" if tool_context else text
        self.save_message(chat_id, "user", f"{sender}: {user_message}")
        history = self.get_chat_history(chat_id)

        try:
            response = await self.query_ollama(history)
            clean = response.replace("TASK_COMPLETE", "").strip()
            clean = strip_name_prefix(clean, self.name)

            # ── Simon: check for DISPATCH commands ───────────────────────────
            if self.is_simon and self.commander:
                assignments = self.parse_dispatch(clean)
                if assignments:
                    job_id = f"job_{int(time.time())}"
                    visible = "\n".join(
                        l for l in clean.splitlines()
                        if not l.startswith("DISPATCH:")
                    ).strip()
                    dispatch_summary = "\n".join(
                        f"  → {aid.replace('_', ' ').title()}: {inst[:80]}..."
                        for aid, inst in assignments.items()
                    )
                    notify = f"{visible}\n\n<b>Dispatching team:</b>\n{dispatch_summary}"
                    await update.message.reply_text(
                        f"<b>{self.name}:</b> {notify}", parse_mode="HTML"
                    )
                    self.save_message(chat_id, "assistant", f"{self.name}: {visible}")
                    self.commander.create_job(job_id, assignments)
                    return

            if clean:
                await update.message.reply_text(
                    f"<b>{self.name}:</b> {clean}", parse_mode="HTML"
                )
                self.save_message(chat_id, "assistant", f"{self.name}: {clean}")

            if self.is_task_complete(response):
                self.mark_task_complete(chat_id)

        except Exception as e:
            logger.error(f"[{self.name}] Error: {e}")
            if not is_group:
                await update.message.reply_text(f"Error: {str(e)}")

    # ─── Non-Simon: handle Simon dispatch ────────────────────────────────────

    async def _handle_dispatch(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                               chat_id: str, text: str):
        try:
            task_id = text.split("[TASK:")[1].split("]")[0]
            instruction = text.split("Simon says:\n\n")[1].split("\n\nReport back")[0].strip()
        except Exception:
            instruction = text
            task_id = f"unknown_{int(time.time())}"

        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        messages = [{"role": "user", "content": f"Simon orders: {instruction}"}]
        response = await self.query_ollama(messages)
        clean = strip_name_prefix(response.replace("TASK_COMPLETE", "").strip(), self.name)

        report_msg = f"REPORT:{task_id}:{clean}"
        simon_token = os.environ.get(SIMON_TOKEN_ENV)
        simon_chat_id = self.redis.get(f"agent:simon_bately:serge_chat_id")

        if simon_token and simon_chat_id:
            req.post(
                f"https://api.telegram.org/bot{simon_token}/sendMessage",
                json={"chat_id": simon_chat_id, "text": report_msg},
                timeout=15
            )

        await update.message.reply_text(
            f"<b>{self.name}:</b> On it. Report sent to Simon.",
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
