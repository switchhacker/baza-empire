"""
Baza Empire — Sam Axe
Imaging, Graphics, Media, Architectural & Engineering Visualization
"""
import os
import re
import asyncio
import logging
import tempfile
from pathlib import Path
from telegram import Update, InputFile
from telegram.constants import ChatAction
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from core.base_agent import BaseAgent
from core.memory import save_message, get_history

logger = logging.getLogger(__name__)

IMAGE_KEYWORDS = [
    "generate", "create", "draw", "render", "design",
    "logo", "brand", "visual", "blueprint", "floor plan", "elevation",
    "architectural", "engineering", "diagram", "sketch", "concept", "art",
    "illustration", "banner", "poster", "mockup", "3d render",
]

# These phrases mean "retrieve/send an existing image" — never trigger generation
SHOW_PHRASES = [
    "show me", "send me", "show the", "send the", "show those", "send those",
    "show it", "see it", "see the image", "see those", "let me see",
    "view it", "view the", "display", "see what you made", "see what you generated",
    "show what", "send what",
]

MAX_HISTORY = 10


class SamAxe(BaseAgent):
    AGENT_ID = "sam_axe"
    MODEL = "qwen3-vl:latest"
    TOKEN_ENV = "TELEGRAM_SAM_AXE"
    USE_GPU_POOL = True

    def __init__(self):
        super().__init__()
        # Per-chat list of image paths generated this session: {chat_id: [path, ...]}
        self._session_images: dict = {}

    # build_system_prompt() inherited from BaseAgent — Sam's persona now lives
    # in agents/sam_axe/persona/{IDENTITY,SOUL,MISSION,USER}.md

    def _find_printable_file(self, text: str) -> str | None:
        """Override base to also check Sam's session images."""
        # Try session images first (Sam-specific)
        # We need a chat_id but don't have it here — check all sessions
        for chat_id, paths in self._session_images.items():
            existing = [p for p in paths if Path(p).exists()]
            if existing:
                return existing[-1]
        # Fall back to base class logic
        return super()._find_printable_file(text)

    def _is_show_request(self, text: str) -> bool:
        """Serge wants to SEE already-generated images — do NOT regenerate."""
        t = text.lower()
        # Explicit file path in message
        if re.search(r'/mnt/\S+\.(?:png|jpg|jpeg|webp)', text):
            return True
        return any(phrase in t for phrase in SHOW_PHRASES)

    def _is_image_request(self, text: str) -> bool:
        t = text.lower()
        # Never treat "show me" as a generate request
        if self._is_show_request(text):
            return False
        return any(kw in t for kw in IMAGE_KEYWORDS)

    def _extract_paths_from_text(self, text: str) -> list:
        """Pull any /mnt/... image paths mentioned in a message."""
        return re.findall(r'/mnt/\S+\.(?:png|jpg|jpeg|webp)', text)

    def _record_generated(self, chat_id: int, path: str):
        self._session_images.setdefault(chat_id, [])
        if path not in self._session_images[chat_id]:
            self._session_images[chat_id].append(path)
        # Persist: overwrite last_image_generated + keep rolling list (last 10, deduped)
        self.remember("last_image_generated", path, "images")
        existing = [p.strip() for p in (self.recall("session_images") or "").splitlines() if p.strip()]
        # Replace any entry sharing the same timestamp prefix to avoid stale variants
        stem = Path(path).stem
        ts_prefix = stem.split("_")[0] if "_" in stem else stem[:10]
        existing = [p for p in existing if not Path(p).stem.startswith(ts_prefix)]
        existing.append(path)
        self.remember("session_images", "\n".join(existing[-10:]), "images")

    def _get_session_images(self, chat_id: int) -> list:
        """Return image paths for this chat, filtered to files that exist on disk.
        Priority: in-memory session → last_image_generated memory → filesystem most-recent."""
        import glob as _glob

        # 1. In-memory session dict — most reliable, only populated this run
        paths = [p for p in self._session_images.get(chat_id, []) if Path(p).exists()]
        if paths:
            return paths

        # 2. Persistent memory: last image generated (survives restarts)
        last = self.recall("last_image_generated")
        if last and Path(last).exists():
            return [last]

        # 3. Last resort: most recent file on disk
        gen_dir = "/mnt/empirepool/media/generated"
        recent = sorted(
            _glob.glob(f"{gen_dir}/*.png") + _glob.glob(f"{gen_dir}/*.jpg"),
            key=lambda f: os.path.getmtime(f),
            reverse=True
        )
        return recent[:1]  # only the single most recent file

    async def _send_images(self, chat_id: int, paths: list, context) -> int:
        """Send a list of image file paths as Telegram photos. Returns count sent."""
        sent = 0
        for img_path in paths:
            p = Path(img_path)
            if not p.exists():
                await context.bot.send_message(chat_id=chat_id, text=f"⚠️ File not found: {img_path}")
                continue
            try:
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
                with open(img_path, "rb") as f:
                    await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=InputFile(f, filename=p.name),
                        caption=f"🎨 {p.stem[:100]}"
                    )
                sent += 1
                logger.info(f"[sam_axe] Sent photo: {img_path}")
            except Exception as e:
                logger.error(f"[sam_axe] Failed to send photo {img_path}: {e}")
                await context.bot.send_message(chat_id=chat_id, text=f"📁 Saved at: {img_path}")
        return sent

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        text = update.message.text or ""

        if not text.strip():
            return

        logger.info(f"[{self.AGENT_ID}] Message from {chat_id}: {text[:80]}")
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        save_message(chat_id, self.AGENT_ID, "user", text)
        self.journal("message_received", f"User: {text[:200]}", chat_id=chat_id)

        # ── "Show me" intercept — send already-generated images, skip LLM ──
        if self._is_show_request(text):
            # Paths explicitly mentioned in the message always take priority
            explicit_paths = self._extract_paths_from_text(text)
            paths_to_send  = explicit_paths if explicit_paths else self._get_session_images(chat_id)

            if paths_to_send:
                # Filter to files that actually exist before attempting to send
                existing = [p for p in paths_to_send if Path(p).exists()]
                missing  = [p for p in paths_to_send if not Path(p).exists()]
                if existing:
                    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
                    sent = await self._send_images(chat_id, existing, context)
                    reply = f"📤 {sent} image(s) sent."
                    if missing:
                        reply += f"\n⚠️ {len(missing)} path(s) no longer on disk."
                else:
                    reply = "⚠️ Files are no longer on disk. Ask me to regenerate."
            else:
                reply = (
                    "No images in session memory. "
                    "Ask me to generate one, or provide the full path: "
                    "`/mnt/empirepool/media/generated/<filename>.png`"
                )

            save_message(chat_id, self.AGENT_ID, "assistant", reply)
            await self._send_response(context.bot, chat_id, reply)
            return

        # ── Print request intercept — run skill directly, skip LLM ──
        if self._is_print_request(text):
            await context.bot.send_message(chat_id=chat_id, text="Sending to printer...")
            reply = await self._handle_print_request(text, chat_id)
            save_message(chat_id, self.AGENT_ID, "assistant", reply)
            await self._send_response(context.bot, chat_id, reply)
            return

        history = get_history(chat_id, self.AGENT_ID, limit=MAX_HISTORY)
        messages = [{"role": h["role"], "content": h["content"]} for h in history]
        loop = asyncio.get_event_loop()

        system = self.build_system_prompt()

        if self._is_image_request(text):
            # Send "generating" notice immediately so user knows it's working
            await context.bot.send_message(
                chat_id=chat_id,
                text="🎨 Generating... this takes ~1-2 min"
            )
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)

            # For image requests, remind him strongly to actually run the skill
            augmented_messages = messages + [{
                "role": "user",
                "content": (
                    f"{text}\n\n"
                    "[IMPORTANT: Do NOT describe an image — actually generate it. "
                    "Run ##SKILL: generate_image## with a detailed prompt RIGHT NOW. "
                    "Report the real output path when done.]"
                )
            }]
            response = await loop.run_in_executor(
                None, self.llm_chat, augmented_messages, system
            )
        else:
            messages_with_user = messages + [{"role": "user", "content": text + "\n\n[FORMATTING: No markdown. No ### headers. No ALL CAPS. No ** bold. Use emoji for structure and plain text only.]"}]
            response = await loop.run_in_executor(
                None, self.llm_chat, messages_with_user, system
            )

        if not response:
            response = "_(no response)_"

        # ── Execute any ##SKILL:## calls the LLM emitted ──────────────────
        import json as _json
        response, skill_results = self.skills.parse_and_run(response, chat_id=chat_id)
        successful = [r for r in skill_results if r.get("success")]
        failed     = [r for r in skill_results if not r.get("success")]

        # ── Send generated images IMMEDIATELY (before any LLM pass) ───────
        sent_images = []
        for r in successful:
            out = r.get("output", "")
            img_path = None
            try:
                parsed = _json.loads(out)
                img_path = parsed.get("image_path")
            except Exception:
                match = re.search(r'(/[^\s"]+\.(?:png|jpg|jpeg|webp))', out)
                if match:
                    img_path = match.group(1)

            if img_path and Path(img_path).exists():
                self._record_generated(chat_id, img_path)
                sent_images.append(img_path)
                try:
                    await context.bot.send_chat_action(
                        chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO
                    )
                    with open(img_path, "rb") as f:
                        await context.bot.send_photo(
                            chat_id=chat_id,
                            photo=InputFile(f, filename=Path(img_path).name),
                            caption=f"🎨 {Path(img_path).stem[:100]}"
                        )
                    logger.info(f"[sam_axe] Sent photo: {img_path}")
                except Exception as e:
                    logger.error(f"[sam_axe] Failed to send photo {img_path}: {e}")
                    # Fall back: tell user where it is
                    await context.bot.send_message(chat_id=chat_id, text=f"📁 Saved: {img_path}")
            elif img_path:
                logger.warning(f"[sam_axe] Skill returned path that doesn't exist: {img_path}")
                await context.bot.send_message(chat_id=chat_id, text=f"⚠️ Generated file not found on disk: {img_path}")

        # ── Pass 2: reformat LLM reply with real skill output ─────────────
        if successful:
            skill_data = "\n\n".join(
                f"[{r['skill']} result]\n{r['output']}" for r in successful
            )
            reformat_msgs = [
                {"role": "user", "content": text},
                {"role": "assistant", "content": response},
                {"role": "user", "content": (
                    f"Skill results:\n{skill_data}\n\n"
                    "The image has already been sent to Telegram. "
                    "Just confirm it briefly — 1-2 sentences, no markdown, no fabrication."
                )}
            ]
            reformatted = await loop.run_in_executor(
                None, self.llm_chat, reformat_msgs, system
            )
            if reformatted:
                response = reformatted

        # ── Report any skill failures ──────────────────────────────────────
        if failed:
            response += "\n\n⚠️ " + "; ".join(
                f"{r.get('skill','?')}: {r.get('error','unknown')}" for r in failed
            )

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

    # ── Photo Handler — analyze uploaded images ───────────────────────────────

    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """When user sends a photo, analyze it via cloud vision and return an editable description."""
        chat_id = update.effective_chat.id
        caption = update.message.caption or ""

        logger.info(f"[sam_axe] Photo received from {chat_id}, caption: {caption[:60]}")
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        # Download the photo (get highest resolution)
        photo = update.message.photo[-1]  # largest size
        tg_file = await photo.get_file()
        # Save to artifacts under proj-ahb123 so it shows in the dashboard
        import uuid, json as _json2, datetime as _dt
        framework_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        artifacts_dir = os.path.join(framework_dir, "dashboard", "artifacts", "proj-ahb123")
        os.makedirs(artifacts_dir, exist_ok=True)
        filename = f"sam_axe_{_dt.datetime.now().strftime('%Y%m%d_%H%M')}_{uuid.uuid4().hex[:6]}.jpg"
        local_path = os.path.join(artifacts_dir, filename)
        await tg_file.download_to_drive(local_path)
        # Write .meta sidecar for artifact scanner
        try:
            with open(local_path + ".meta", "w") as mf:
                _json2.dump({"agent_id": "sam_axe", "task_id": "", "created_at": _dt.datetime.now().isoformat()}, mf)
        except Exception:
            pass
        logger.info(f"[sam_axe] Photo saved to artifacts: {local_path}")

        save_message(chat_id, self.AGENT_ID, "user", f"[Photo uploaded: {filename}] {caption}")
        self.journal("photo_received", f"Photo: {filename}, caption: {caption[:200]}", chat_id=chat_id)

        # Analyze via cloud vision
        await context.bot.send_message(chat_id=chat_id, text="Analyzing image... one moment.")
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        loop = asyncio.get_event_loop()
        mode = "describe_for_agents" if not caption else "analyze"
        prompt = caption if caption else "Describe this image in detail for an architect. Note room type, dimensions, materials, condition, and any work needed."

        result = await loop.run_in_executor(
            None, self.skills.run, "analyze_image",
            {"image_path": local_path, "prompt": prompt, "mode": mode}, chat_id
        )

        if result.get("success"):
            output = result.get("output", "")
            # Try to extract just the analysis text
            import json as _json
            try:
                parsed = _json.loads(output.split("\n")[-1])
                description = parsed.get("analysis", output)
            except Exception:
                # The output may have the text before the JSON
                description = output.split("\n---\n")[-1] if "\n---\n" in output else output
                # Strip the JSON at the end if present
                if '{"success"' in description:
                    description = description[:description.index('{"success"')].strip()

            response = (
                f"Here is my analysis of the image:\n\n"
                f"{description}\n\n"
                f"Copy this description, edit as needed, and send it back to me. "
                f"I will generate a new image based on your edited description."
            )
            # Remember the analysis for context
            self.remember("last_image_analysis", description[:500], "images")
            self.remember("last_analyzed_photo", local_path, "images")
        else:
            error = result.get("error", result.get("output", "Unknown error"))
            response = (
                f"Could not analyze the image: {error}\n\n"
                f"The image was saved at: {local_path}\n"
                f"You can describe what you want and I will generate it."
            )

        save_message(chat_id, self.AGENT_ID, "assistant", response)
        await self._send_response(context.bot, chat_id, response)

    # ── Override run() to add photo handler ──────────────────────────────────

    async def run(self):
        token = os.environ.get(self.TOKEN_ENV)
        if not token:
            raise ValueError(f"[{self.AGENT_ID}] Missing token: {self.TOKEN_ENV}")

        app = Application.builder().token(token).build()
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        app.add_handler(MessageHandler(filters.PHOTO, self.handle_photo))

        logger.info(f"[{self.AGENT_ID}] Starting Telegram bot (with photo handler)...")

        async with app:
            await app.initialize()
            await app.start()
            asyncio.ensure_future(self._heartbeat_loop())
            asyncio.ensure_future(self._artifact_context_loop())
            await app.updater.start_polling(drop_pending_updates=True)
            try:
                await asyncio.Event().wait()
            finally:
                await app.updater.stop()
                await app.stop()

    def _auto_remember(self, chat_id: int, user_msg: str, agent_reply: str):
        super()._auto_remember(chat_id, user_msg, agent_reply)
        style_match = re.search(
            r'(?:style|look|aesthetic)[:\s]+([^\.\,\n]+)',
            user_msg, re.IGNORECASE
        )
        if style_match:
            self.remember("last_style_requested", style_match.group(1).strip()[:100], "style")


if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO)
    agent = SamAxe()
    asyncio.run(agent.run())
