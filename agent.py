"""
Baza Empire Agent — runs as a single instance per agent.
Usage: python agent.py --agent claw_batto
"""

import os
import sys
import yaml
import asyncio
import logging
import argparse
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from telegram.error import BadRequest, TimedOut, RetryAfter
from core.ollama_client import chat_stream_pooled, both_instances_available
from core.memory import init_db, save_message, get_history, get_active_task, set_task, complete_task
from core.coordinator import should_agent_respond, build_group_context, is_task_complete

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
log = logging.getLogger(__name__)

CONTEXT_LIMIT = 40        # messages pulled from DB for context
HISTORY_IN_PROMPT = 20    # messages passed into the model prompt
SNAP_THRESHOLD = 60       # chars — messages shorter than this get a snap reply

with open("config/agents.yaml") as f:
    CONFIG = yaml.safe_load(f)


def load_agent(agent_id: str) -> dict:
    agent = CONFIG["agents"][agent_id].copy()
    agent["id"] = agent_id
    token_env = agent["telegram_token_env"]
    agent["token"] = os.environ.get(token_env)
    if not agent["token"]:
        raise ValueError(f"Missing env var: {token_env}")
    return agent


def is_snap_message(text: str) -> bool:
    """Short, simple messages that deserve a quick punchy reply."""
    return len(text.strip()) < SNAP_THRESHOLD and "\n" not in text.strip()


def build_snap_suffix() -> str:
    return (
        "\n\nIMPORTANT: This is a short/simple message. Reply in 1-3 sentences max. "
        "Be direct and punchy. No lists, no explanations, no padding."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE, agent: dict):
    message = update.message
    if not message or not message.text:
        return

    chat_id = message.chat_id
    user_text = message.text
    is_group = message.chat.type in ["group", "supergroup"]
    agent_id = agent["id"]

    if is_group:
        if not should_agent_respond(agent_id, user_text, is_group=True):
            return
        if message.from_user and message.from_user.is_bot:
            return

    log.info(f"[{agent['name']}] chat_id={chat_id} snap={is_snap_message(user_text)} msg={user_text[:80]}")

    history = get_history(chat_id, agent_id=agent_id, limit=CONTEXT_LIMIT)
    current_task = get_active_task(chat_id, agent_id=agent_id)

    if not current_task:
        set_task(chat_id, user_text[:500], agent_id=agent_id)

    messages = []

    # Inject group context summary
    if is_group and history:
        group_ctx = build_group_context(history, current_task)
        messages.append({"role": "user", "content": f"[Context]\n{group_ctx}"})
        messages.append({"role": "assistant", "content": "Got it."})

    # Inject recent conversation history
    for msg in history[-HISTORY_IN_PROMPT:]:
        role = "assistant" if msg.get("agent") == agent_id else "user"
        messages.append({"role": role, "content": msg["content"]})

    # Snap mode — append instruction to keep it short
    final_text = user_text
    if is_snap_message(user_text):
        final_text = user_text + build_snap_suffix()

    messages.append({"role": "user", "content": final_text})

    save_message(chat_id, agent_id, "user", user_text)

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    sent = await message.reply_text("✍️")

    try:
        loop = asyncio.get_event_loop()

        def _run_inference():
            full = ""
            for chunk in chat_stream_pooled(
                model=agent["model"],
                messages=messages,
                system_prompt=agent["system_prompt"],
                agent_id=agent_id
            ):
                full += chunk
            return full

        full_response = await loop.run_in_executor(None, _run_inference)

        if not full_response:
            await sent.edit_text("_(no response)_")
            return

        if is_task_complete(full_response):
            complete_task(chat_id, agent_id=agent_id)
            full_response = full_response.replace("TASK_COMPLETE", "").strip()
            full_response = full_response.replace("task_complete", "").strip()

        save_message(chat_id, agent_id, "assistant", full_response)

        # Send final response — split if over 4000 chars
        chunks = [full_response[i:i+4000] for i in range(0, len(full_response), 4000)]
        try:
            await sent.edit_text(chunks[0])
        except (BadRequest, TimedOut):
            await message.reply_text(chunks[0])

        for extra in chunks[1:]:
            await asyncio.sleep(0.3)
            await message.reply_text(extra)

    except Exception as e:
        log.error(f"Inference error: {e}", exc_info=True)
        try:
            await sent.edit_text(f"⚠️ Error: {str(e)}")
        except Exception:
            pass


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE, agent: dict):
    await update.message.reply_text(
        f"👋 {agent['name']} online.\n{agent['role']}\nModel: {agent['model']}"
    )


async def handle_reset(update: Update, context: ContextTypes.DEFAULT_TYPE, agent: dict):
    chat_id = update.message.chat_id
    agent_id = agent["id"]
    complete_task(chat_id, agent_id=agent_id)
    try:
        from core.memory import get_conn
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM messages WHERE chat_id = %s AND agent_id = %s", (chat_id, agent_id))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        log.error(f"Reset error: {e}")
    await update.message.reply_text("Context cleared. Ready.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", required=True)
    args = parser.parse_args()

    init_db()

    status = both_instances_available()
    log.info(f"GPU status — AMD:11434={status['amd_vulkan']} | NVIDIA:11435={status['nvidia_cuda']}")

    if not status['amd_vulkan'] and not status['nvidia_cuda']:
        log.error("No Ollama instances running! Start with: ollama serve")
        sys.exit(1)

    agent = load_agent(args.agent)
    log.info(f"Starting agent: {agent['name']} | model: {agent['model']}")

    app = Application.builder().token(agent["token"]).build()

    app.add_handler(CommandHandler("start", lambda u, c: handle_start(u, c, agent)))
    app.add_handler(CommandHandler("reset", lambda u, c: handle_reset(u, c, agent)))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        lambda u, c: handle_message(u, c, agent)
    ))

    log.info(f"{agent['name']} is listening...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
