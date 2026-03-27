"""
Baza Empire — Sam Axe
Imaging, Graphics, Media, Architectural & Engineering Visualization
"""
import re
import asyncio
import logging
from pathlib import Path
from telegram import Update, InputFile
from telegram.constants import ChatAction
from telegram.ext import ContextTypes
from core.base_agent import BaseAgent
from core.memory import save_message, get_history

logger = logging.getLogger(__name__)

IMAGE_KEYWORDS = [
    "generate", "create", "draw", "render", "design", "image", "photo",
    "logo", "brand", "visual", "blueprint", "floor plan", "elevation",
    "architectural", "engineering", "diagram", "sketch", "concept", "art",
    "illustration", "banner", "poster", "mockup", "3d", "render"
]

MAX_HISTORY = 10


class SamAxe(BaseAgent):
    AGENT_ID = "sam_axe"
    MODEL = "qwen2.5:14b"
    TOKEN_ENV = "TELEGRAM_SAM_AXE"
    USE_GPU_POOL = True

    def build_system_prompt(self, extra: str = "") -> str:
        extra_instructions = """
You are Sam Axe — Creative Director, Imaging Master, and Visual Intelligence of the Baza Empire.
You report directly to Serge (Master Orchestrator).

== YOUR DOMAIN ==
- AI image generation (Stable Diffusion WebUI at http://localhost:7860)
- Brand identity, logos, visual kits
- Architectural visualizations, floor plans, elevations, site plans
- Engineering diagrams and technical illustrations
- Marketing materials, banners, mockups
- Media management at /mnt/empirepool/media/generated/

== PERSONALITY ==
Creative, precise, visual thinker. You speak in terms of composition, lighting, and style.
When asked for visuals, you deliver — no excuses, just results.
Short replies for ops talk. Descriptive when discussing creative direction.

== CRITICAL RULES ==
1. NEVER describe an image you didn't actually generate — run the skill IMMEDIATELY.
2. NEVER fabricate file paths. Report actual output paths from skill results.
3. If SD WebUI is offline, say so clearly.
4. Always save generated images to /mnt/empirepool/media/generated/
5. When writing image prompts: be specific about style, lighting, composition, color palette, camera angle.
6. When someone says "show me" or "generate" — DO NOT ask for more info, just run the skill NOW.
7. The image will be automatically sent to Telegram after the skill runs — just confirm it.

== IMAGE REQUEST WORKFLOW ==
1. Run ##SKILL: generate_image## immediately with a detailed prompt
2. The system sends the image — you confirm: "Generated [description]. Sent above."

== SKILLS AVAILABLE ==
Image generation:
  ##SKILL: generate_image {"prompt": "detailed prompt", "steps": 30, "width": 512, "height": 512}##
  ##SKILL: generate_logo {"name": "Company Name", "style": "modern minimal", "colors": "blue, white"}##
  ##SKILL: enhance_image {"image_path": "/path/to/image.png"}##
  ##SKILL: remove_bg {"image_path": "/path/to/image.png"}##

Brand & creative:
  ##SKILL: brand_brief {"company": "AHBCO LLC", "industry": "construction"}##

Data (when needed):
  ##SKILL: crypto_prices {"coins": ["monero", "ravencoin", "bitcoin"]}##

== ARCHITECTURAL & ENGINEERING NOTES ==
For AHBCO LLC projects: default style is clean, modern construction/architecture visualization.
Preferred palette: navy blue, white, warm wood tones, concrete grey.
For floor plans: top-down, clean lines, labeled rooms, metric or imperial as specified.
For elevations: front-facing, realistic lighting, show materials clearly.
"""
        return super().build_system_prompt(extra_instructions)

    def _is_image_request(self, text: str) -> bool:
        t = text.lower()
        return any(kw in t for kw in IMAGE_KEYWORDS)

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

        if self._is_image_request(text):
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
        response, skill_results = self.skills.parse_and_run(response, chat_id=chat_id)

        # ── Pass 2: if skills ran, let LLM reformat with real output ──────
        successful = [r for r in skill_results if r.get("success")]
        if successful:
            skill_data = "\n\n".join(
                f"[{r['skill']} result]\n{r['output']}" for r in successful
            )
            import json as _json
            reformat_msgs = [
                {"role": "user", "content": text},
                {"role": "assistant", "content": response},
                {"role": "user", "content": (
                    f"Skill results:\n{skill_data}\n\n"
                    "Report the result concisely. "
                    "If an image was generated, state the filename and confirm it will be sent. "
                    "No markdown, no fabrication."
                )}
            ]
            reformatted = await loop.run_in_executor(
                None, self.llm_chat, reformat_msgs, system
            )
            if reformatted:
                response = reformatted

        # ── Report any skill failures ──────────────────────────────────────
        failed = [r for r in skill_results if not r.get("success")]
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

        # ── Send any generated images as Telegram photos ──────────────────
        image_sent = False
        for r in successful:
            out = r.get("output", "")
            # Check skill output for an image_path field (JSON or plain path)
            img_path = None
            try:
                import json as _json
                parsed = _json.loads(out)
                img_path = parsed.get("image_path")
            except Exception:
                match = re.search(r'(/[^\s"]+\.(?:png|jpg|jpeg|webp))', out)
                if match:
                    img_path = match.group(1)

            if img_path and Path(img_path).exists():
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
                    image_sent = True
                    logger.info(f"[sam_axe] Sent photo: {img_path}")
                except Exception as e:
                    logger.error(f"[sam_axe] Failed to send photo {img_path}: {e}")
                    response += f"\n\n📁 Saved: `{img_path}`"

        await self._send_response(context.bot, chat_id, response)

    def _auto_remember(self, chat_id: int, user_msg: str, agent_reply: str):
        super()._auto_remember(chat_id, user_msg, agent_reply)

        img_match = re.search(
            r'(/mnt/empirepool/\S+\.(?:png|jpg|jpeg|webp))',
            agent_reply, re.IGNORECASE
        )
        if img_match:
            self.remember("last_image_generated", img_match.group(1), "images")

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
