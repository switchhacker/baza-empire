"""
Baza Empire — Claw Batto
Desktop Linux Engineer, Full-Stack Dev, DevOps
"""
import re
import asyncio
import logging
import time
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes
from core.base_agent import BaseAgent
from core.memory import save_message, get_history

logger = logging.getLogger(__name__)

STATUS_KEYWORDS = [
    "status", "health", "mining", "service", "docker", "disk", "ollama",
    "running", "check", "monitor", "uptime", "brief", "everything"
]

MAX_HISTORY = 10


class ClawBatto(BaseAgent):
    AGENT_ID = "claw_batto"
    MODEL = "qwen2.5:14b"
    TOKEN_ENV = "TELEGRAM_CLAW_BATTO"
    USE_GPU_POOL = True

    def build_system_prompt(self, extra: str = "") -> str:
        extra_instructions = """
You are Claw Batto — Lead Engineer of the Baza Empire. Linux, full-stack dev, DevOps.
You report directly to Serge (Master Orchestrator).

== PERSONALITY ==
Terse. Technical. No fluff. Give commands, paths, and facts — not explanations unless asked.
Short replies unless doing a full system report with real data injected.

== STACK ==
- Main rig: baza (Ryzen 7 5700G, RTX 3070, RX 6700 XT, Ubuntu 24.04)
- NUC: Intel i7-10710U, 64GB RAM, Ubuntu 24.04
- ZFS pool: empirepool (RAIDZ2, ~42.9TB usable, /mnt/empirepool)
- Services: Ollama (11437), SD WebUI (7860), Nextcloud (8080), Gitea, Mosquitto
- Mining: XMRig (CPU/XMR), T-Rex (NVIDIA/RVN), TeamRedMiner (AMD/RVN)
- Tailscale IP: 100.127.118.103

== CRITICAL RULES ==
1. NEVER fabricate system data, hashrates, temperatures, or service states.
2. When live data is injected into your context — use those exact values.
3. If data is not available, say "data unavailable" — don't guess.
4. Keep it tight. Serge is busy.

== STATUS REPORT FORMAT ==
When live data is provided:
━━━━━━━━━━━━━━━━
SYSTEM STATUS — [real timestamp]
━━━━━━━━━━━━━━━━
MINING: [exact values]
SERVICES: [exact values]
DISK: [exact values]
━━━━━━━━━━━━━━━━
"""
        return super().build_system_prompt(extra_instructions)

    def _is_status_request(self, text: str) -> bool:
        t = text.lower()
        return any(kw in t for kw in STATUS_KEYWORDS)

    def _fetch_live_data(self) -> str:
        sections = []

        r = self.skills.run("mining_status", {})
        sections.append(r["output"] if r.get("success") and r.get("output") else "MINING STATUS: data unavailable")

        r = self.skills.run("system_health", {})
        sections.append(r["output"] if r.get("success") and r.get("output") else "SYSTEM HEALTH: data unavailable")

        return "\n\n".join(sections)

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

        if self._is_status_request(text):
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            live_data = await loop.run_in_executor(None, self._fetch_live_data)

            system = self.build_system_prompt()
            augmented_system = (
                system
                + f"\n\n== LIVE DATA (real values fetched right now — use these exactly) ==\n"
                + live_data
                + "\n== END LIVE DATA ==\n"
            )
            augmented_messages = messages + [{
                "role": "user",
                "content": (
                    f"{text}\n\n"
                    "[Live data injected above. Use ONLY those exact values. Never guess.]"
                )
            }]
            response = await loop.run_in_executor(
                None, self.llm_chat, augmented_messages, augmented_system
            )
        else:
            system = self.build_system_prompt()
            messages_with_user = messages + [{"role": "user", "content": text + "\n\n[FORMATTING: No markdown. No ### headers. No ALL CAPS. No ** bold. Use emoji for structure and plain text. Code blocks only for actual code.]"}]
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

        svc_match = re.search(r'(baza-[\w-]+\.service)', user_msg + agent_reply)
        if svc_match:
            self.remember("last_service_discussed", svc_match.group(1), "services")

        path_match = re.search(r'(/home/\S+|/mnt/\S+|/etc/\S+)', user_msg)
        if path_match:
            self.remember("last_path_discussed", path_match.group(1), "paths")


if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO)
    agent = ClawBatto()
    asyncio.run(agent.run())
