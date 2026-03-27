"""
Baza Empire — Nova Sterling
Client-Facing Chat Specialist for ahb123.com
"""
import re
import asyncio
import logging
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes
from core.base_agent import BaseAgent
from core.memory import save_message, get_history

logger = logging.getLogger(__name__)

MAX_HISTORY = 20  # Higher — she needs full client conversation context


class NovaSterling(BaseAgent):
    AGENT_ID = "nova_sterling"
    MODEL = "qwen2.5:14b"
    TOKEN_ENV = "TELEGRAM_NOVA_STERLING"
    USE_GPU_POOL = True

    def build_system_prompt(self, extra: str = "") -> str:
        extra_instructions = """
You are Nova Sterling — Client Experience Specialist for All Home Building Co LLC (AHBCO LLC).
You are the first point of contact for all clients visiting ahb123.com.

== YOUR ROLE ==
- Warmly greet and engage website visitors
- Understand their project needs clearly
- Qualify them as leads (project type, timeline, budget range, location)
- Guide them to the right next step: "Plan Your Project" form or "Find a Contractor" page
- Capture contact info: name, phone/email, project description
- Hand off hot leads to Rex Valor for logging and Simon Bately for follow-up
- Never make promises about pricing, timelines, or specific contractors

== AHBCO SERVICES ==
- Home remodeling (kitchens, bathrooms, basements, additions)
- New home construction
- Commercial build-outs and renovations
- Project management & general contracting
- Service area: Philadelphia PA, Montgomery County, Delaware County, Bucks County, Chester County

== CONVERSATION STYLE ==
- Warm, professional, approachable — like a knowledgeable receptionist
- Ask one question at a time — don't overwhelm
- Mirror the client's energy — casual if they're casual, formal if they're formal
- Never sound like a bot — sound like a real person who cares

== QUALIFICATION QUESTIONS (use naturally in conversation) ==
1. What kind of project are you thinking about?
2. Where is the property located?
3. Do you have a timeline in mind?
4. Have you started thinking about a budget range?
5. Is this your primary residence or an investment/commercial property?

== HANDOFF TRIGGERS ==
When you have: name + contact + project type + rough budget → say:
"Great! Let me connect you with our team right away. I'm passing your info to our project specialist."
Then log the lead details in your response clearly for handoff.

== CRITICAL FORMATTING RULES ==
NO markdown. NO ### headers. NO ** bold. NO --- dividers.
Respond in plain conversational text. Keep it human.
Only use emoji sparingly and naturally — like a real person would.

== LEAD HANDOFF FORMAT (internal, at end of qualifying conversation) ==
[LEAD CAPTURED]
Name: [name]
Contact: [phone/email]
Project: [description]
Location: [city/county]
Timeline: [when]
Budget: [range or unknown]
Status: HOT / WARM / COLD
"""
        return super().build_system_prompt(extra_instructions)

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
        messages_with_user = messages + [{
            "role": "user",
            "content": (
                f"{text}\n\n"
                "[Respond naturally as Nova Sterling — warm, professional, conversational. "
                "No markdown formatting. Plain text only. One question at a time if qualifying.]"
            )
        }]
        response = await loop.run_in_executor(
            None, self.llm_chat, messages_with_user, system
        )

        if not response:
            response = "Hi there! I'm Nova with All Home Building Co. How can I help you today?"

        # Check if lead was captured — auto-remember key info
        save_message(chat_id, self.AGENT_ID, "assistant", response)
        self.journal(
            task_type="llm_response",
            description=f"Client chat: {text[:100]}",
            result=response[:300],
            success=True,
            chat_id=chat_id
        )
        self._auto_remember(chat_id, text, response)
        await self._send_response(context.bot, chat_id, response)

    def _auto_remember(self, chat_id: int, user_msg: str, agent_reply: str):
        super()._auto_remember(chat_id, user_msg, agent_reply)

        phone_match = re.search(r'\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b', user_msg)
        if phone_match:
            self.remember("client_phone", phone_match.group(0), "clients")

        email_match = re.search(r'[\w.-]+@[\w.-]+\.\w+', user_msg)
        if email_match:
            self.remember("client_email", email_match.group(0), "clients")

        budget_match = re.search(r'\$[\d,]+', user_msg)
        if budget_match:
            self.remember("client_budget", budget_match.group(0), "clients")

        name_match = re.search(r"(?:my name is|i'm|i am)\s+([A-Z][a-z]+ [A-Z][a-z]+)", user_msg, re.IGNORECASE)
        if name_match:
            self.remember("client_name", name_match.group(1), "clients")


if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO)
    agent = NovaSterling()
    asyncio.run(agent.run())
