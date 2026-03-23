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
    'mining':         ['mining status', 'miner status', 'check mining'],
    'start_mining':   ['start mining', 'start miners', 'start the mine', 'turn on mining', 'launch mining'],
    'stop_mining':    ['stop mining', 'stop miners', 'turn off mining', 'halt mining', 'pause mining'],
    'earnings':       ['earnings', 'earning', 'how much have i made', 'mining income', 'payout', 'how much xmr'],
    'crypto':         ['crypto price', 'crypto prices', 'coin price', 'xmr price', 'rvn price', 'bitcoin price', 'btc price', 'prices'],
    'disk':           ['disk', 'storage', 'space'],
    'docker':         ['docker', 'container'],
    'generate_image':   ['generate image', 'create image', 'make image', 'draw', 'generate a picture', 'create a picture'],
    'generate_logo':    ['generate logo', 'create logo', 'make a logo', 'design logo'],
    'image_variations': ['image variations', 'variations of', 'make variations'],
    'inpaint':          ['inpaint', 'fill in', 'replace area', 'fix area'],
    'outpaint':         ['outpaint', 'extend image', 'expand image', 'extend canvas'],
    'style_transfer':   ['style transfer', 'apply style', 'make it look like', 'art style'],
    'sketch_to_image':  ['sketch to image', 'sketch to photo', 'turn sketch into'],
    'batch_generate':   ['batch generate', 'generate multiple images', 'multiple images'],
    'analyze_image':    ['analyze image', 'analyse image', 'describe image', 'what is in this image', 'what does this image show'],
    'tag_image':        ['tag image', 'tag this image', 'tag photo', 'label image'],
    'ocr_image':        ['ocr', 'read text', 'extract text', 'text in image', 'transcribe image'],
    'detect_objects':   ['detect objects', 'what objects', 'find objects', 'object detection'],
    'detect_faces':     ['detect faces', 'find faces', 'how many faces', 'face detection'],
    'color_palette':    ['color palette', 'colour palette', 'extract colors', 'dominant colors'],
    'nsfw_check':       ['nsfw check', 'is this nsfw', 'safe for work', 'content check'],
    'read_exif':        ['exif', 'image metadata', 'photo info', 'camera data'],
    'edit_image':       ['edit image', 'edit this image', 'modify image', 'change the image'],
    'enhance_image':    ['enhance image', 'upscale', 'improve image quality', 'sharpen image'],
    'remove_bg':        ['remove background', 'background removal', 'cut out background', 'remove bg'],
    'crop_resize':      ['crop', 'resize image', 'crop image'],
    'rotate_flip':      ['rotate image', 'flip image', 'turn image'],
    'add_watermark':    ['add watermark', 'watermark image', 'stamp image'],
    'color_grade':      ['color grade', 'colour grade', 'adjust brightness', 'adjust contrast', 'adjust saturation'],
    'convert_format':   ['convert image', 'convert to jpg', 'convert to png', 'convert to webp', 'image format'],
    'make_collage':     ['make collage', 'create collage', 'image collage', 'photo collage'],
    'make_gif':         ['make gif', 'create gif', 'animated gif', 'make animation'],
    'smart_scan':       ['scan folder', 'scan photos', 'catalog images', 'smart scan', 'scan my photos'],
    'find_duplicates':  ['find duplicates', 'duplicate images', 'duplicate photos'],
    'batch_rename':     ['batch rename', 'rename images', 'rename photos'],
    'build_gallery':    ['build gallery', 'create gallery', 'html gallery', 'photo gallery'],
    'image_metadata':   ['image metadata', 'file info', 'image info', 'image size'],
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

    if any(kw in text_lower for kw in COMBINED_TRIGGERS['start_mining']):
        tasks['start_mining'] = fire_tool('claw', 'start-mining', {})
    elif any(kw in text_lower for kw in COMBINED_TRIGGERS['stop_mining']):
        tasks['stop_mining'] = fire_tool('claw', 'stop-mining', {})
    elif any(kw in text_lower for kw in COMBINED_TRIGGERS['mining']):
        tasks['mining_status'] = fire_tool('claw', 'mining-status', {})
    if any(kw in text_lower for kw in COMBINED_TRIGGERS['earnings']):
        tasks['mining_earnings'] = fire_tool('sam', 'mining-earnings', {})
    if any(kw in text_lower for kw in COMBINED_TRIGGERS['crypto']):
        tasks['crypto_prices'] = fire_tool('sam', 'crypto-prices',
                                           {'coins': ['monero', 'ravencoin', 'bitcoin']})
    if any(kw in text_lower for kw in COMBINED_TRIGGERS['disk']):
        tasks['disk_usage'] = fire_tool('claw', 'disk-usage', {})
    if any(kw in text_lower for kw in COMBINED_TRIGGERS['docker']):
        tasks['docker_status'] = fire_tool('claw', 'docker-status', {})
    # ── Imaging triggers ──────────────────────────────────────────────────────
    _urls = re.findall(r'https?://\S+|/[\w/.-]+\.(?:jpg|jpeg|png|webp|gif)', text)
    _img  = _urls[0] if _urls else ''
    _folder_match = re.search(r'(/[\w/]+)', text)
    _folder = _folder_match.group(1) if _folder_match else '/mnt/empirepool/media'

    IMG_ROUTING = [
        ('generate_image',  'sam', 'generate-image',    lambda: {'prompt': text}),
        ('generate_logo',   'sam', 'generate-logo',     lambda: {'name': text.replace('logo','').replace('generate','').strip()}),
        ('image_variations','sam', 'image-variations',  lambda: {'image_path': _img} if _img else None),
        ('inpaint',         'sam', 'inpaint-image',     lambda: {'image_path': _img, 'prompt': text} if _img else None),
        ('outpaint',        'sam', 'outpaint-image',    lambda: {'image_path': _img} if _img else None),
        ('style_transfer',  'sam', 'style-transfer',    lambda: {'image_path': _img} if _img else None),
        ('sketch_to_image', 'sam', 'sketch-to-image',   lambda: {'image_path': _img, 'prompt': text} if _img else None),
        ('batch_generate',  'sam', 'batch-generate',    lambda: {'prompts': [text]}),
        ('analyze_image',   'sam', 'analyze-image',     lambda: {'image_path': _img} if _img else None),
        ('tag_image',       'sam', 'tag-image',         lambda: {'image_path': _img} if _img else None),
        ('ocr_image',       'sam', 'ocr-image',         lambda: {'image_path': _img} if _img else None),
        ('detect_objects',  'sam', 'detect-objects',    lambda: {'image_path': _img} if _img else None),
        ('detect_faces',    'sam', 'detect-faces',      lambda: {'image_path': _img} if _img else None),
        ('color_palette',   'sam', 'color-palette',     lambda: {'image_path': _img} if _img else None),
        ('nsfw_check',      'sam', 'nsfw-check',        lambda: {'image_path': _img} if _img else None),
        ('read_exif',       'sam', 'read-exif',         lambda: {'image_path': _img} if _img else None),
        ('edit_image',      'sam', 'edit-image',        lambda: {'image_path': _img, 'instruction': text} if _img else None),
        ('enhance_image',   'sam', 'enhance-image',     lambda: {'image_path': _img} if _img else None),
        ('remove_bg',       'sam', 'remove-background', lambda: {'image_path': _img} if _img else None),
        ('crop_resize',     'sam', 'crop-resize',       lambda: {'image_path': _img} if _img else None),
        ('rotate_flip',     'sam', 'rotate-flip',       lambda: {'image_path': _img} if _img else None),
        ('add_watermark',   'sam', 'add-watermark',     lambda: {'image_path': _img, 'text': '© Baza Empire'} if _img else None),
        ('color_grade',     'sam', 'color-grade',       lambda: {'image_path': _img} if _img else None),
        ('convert_format',  'sam', 'convert-format',    lambda: {'image_path': _img, 'format': 'webp'} if _img else None),
        ('make_collage',    'sam', 'make-collage',      lambda: {'images': _urls} if len(_urls) >= 2 else None),
        ('make_gif',        'sam', 'make-gif',          lambda: {'images': _urls} if len(_urls) >= 2 else None),
        ('smart_scan',      'sam', 'smart-scan',        lambda: {'folder': _folder, 'limit': 5}),
        ('find_duplicates', 'sam', 'find-duplicates',   lambda: {'folder': _folder}),
        ('batch_rename',    'sam', 'batch-rename',      lambda: {'folder': _folder, 'dry_run': True}),
        ('build_gallery',   'sam', 'build-gallery',     lambda: {'folder': _folder}),
        ('image_metadata',  'sam', 'image-metadata',    lambda: {'image_path': _img} if _img else None),
    ]
    for trigger_key, agent_slug, tool_name, inp_builder in IMG_ROUTING:
        if any(kw in text_lower for kw in COMBINED_TRIGGERS.get(trigger_key, [])):
            inp = inp_builder()
            if inp is not None:
                tasks[trigger_key] = fire_tool(agent_slug, tool_name, inp)

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

        if key == 'start_mining':
            parts = [f"{k}: {v}" for k, v in output.items()]
            lines.append(f"MINING STARTED: {' | '.join(parts)}")

        elif key == 'stop_mining':
            parts = [f"{k}: {v}" for k, v in output.items()]
            lines.append(f"MINING STOPPED: {' | '.join(parts)}")

        elif key == 'mining_status':
            lines.append(f"MINING STATUS: {json.dumps(output)}")

        elif key == 'mining_earnings':
            hr = output.get('hashrate_hs', 0)
            paid = output.get('paid_xmr', 0)
            pending = output.get('pending_xmr', 0)
            pending_usd = output.get('pending_usd', 0)
            xmr_price = output.get('xmr_price_usd', 0)
            lines.append(
                f"MINING EARNINGS: Hashrate {hr} H/s | "
                f"Paid {paid} XMR | Pending {pending} XMR (${pending_usd}) | "
                f"XMR price ${xmr_price}"
            )

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

        elif key == 'generate_image':
            path = output.get('path', '')
            seed = output.get('seed', '')
            size = output.get('size', '')
            lines.append(f"IMAGE GENERATED: saved to {path} | size {size} | seed {seed}")

        elif key == 'analyze_image':
            lines.append(f"IMAGE ANALYSIS:\n{output.get('analysis', '')}")

        elif key == 'tag_image':
            tags = ', '.join(output.get('tags', []))
            lines.append(f"IMAGE TAGS: {tags}")

        elif key == 'enhance_image':
            lines.append(f"IMAGE ENHANCED: saved to {output.get('path','')} ({output.get('scale','4')}x via {output.get('method','')})")

        elif key == 'remove_bg':
            lines.append(f"BACKGROUND REMOVED: saved to {output.get('path','')}")

        elif key == 'smart_scan':
            scanned = output.get('scanned', 0)
            catalog = output.get('catalog', [])
            summary = ' | '.join(f"{c['file']}: {c.get('description','?')}" for c in catalog[:3])
            lines.append(f"SMART SCAN: {scanned} images cataloged. Sample: {summary}")

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
