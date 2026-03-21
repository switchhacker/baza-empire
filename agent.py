"""
Baza Empire Agent — runs as a single instance per agent.
Usage: python agent.py --agent brad_gant
"""

import os
import sys
import yaml
import asyncio
import logging
import argparse
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from telegram.error import BadRequest
from core.ollama_client import chat_stream_pooled, both_instances_available
from core.memory import init_db, save_message, get_history, get_active_task, set_task, complete_task
from core.coordinator import should_agent_respond, build_group_context, is_task_complete

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# Load config
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

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE, agent: dict):
    message = update.message
    if not message or not message.text:
        return

    chat_id = message.chat_id
    user_text = message.text
    is_group = message.chat.type in ["group", "supergroup"]
    agent_id = agent["id"]

    # In group chats, check if this agent should respond
    if is_group:
        if not should_agent_respond(agent_id, user_text, is_group=True):
            return
        if message.from_user and message.from_user.is_bot:
            return

    log.info(f"[{agent['name']}] chat_id={chat_id} msg={user_text[:80]}")

    # Get conversation history scoped to THIS agent
    history = get_history(chat_id, agent_id=agent_id, limit=15)
    current_task = get_active_task(chat_id, agent_id=agent_id)

    # Set task if new conversation
    if not current_task:
        set_task(chat_id, user_text[:500], agent_id=agent_id)

    # Build messages for Ollama
    messages = []

    # Add group context if needed
    if is_group and history:
        group_ctx = build_group_context(history, current_task)
        messages.append({"role": "user", "content": f"[Context]\n{group_ctx}"})
        messages.append({"role": "assistant", "content": "Understood, I have the context."})

    # Add recent history (only this agent's own exchanges)
    for msg in history[-8:]:
        role = "assistant" if msg.get("agent") == agent_id else "user"
        messages.append({"role": role, "content": msg["content"]})

    # Add current message
    messages.append({"role": "user", "content": user_text})

    # Save user message
    save_message(chat_id, agent_id, "user", user_text)

    # Send typing indicator
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    # Send placeholder message
    sent = await message.reply_text("✍️ thinking...")

    # Stream response via GPU pool (blocks until a GPU is free)
    full_response = ""
    last_edit_len = 0
    UPDATE_EVERY = 30

    try:
        loop = asyncio.get_event_loop()

        def _stream():
            return list(chat_stream_pooled(
                model=agent["model"],
                messages=messages,
                system_prompt=agent["system_prompt"],
                agent_id=agent_id
            ))

        # Run blocking GPU pool + inference in thread pool
        chunks = await loop.run_in_executor(None, _stream)

        for chunk in chunks:
            full_response += chunk

            if len(full_response) - last_edit_len >= UPDATE_EVERY:
                try:
                    display = full_response[-4000:] if len(full_response) > 4000 else full_response
                    await sent.edit_text(display)
                    last_edit_len = len(full_response)
                    await asyncio.sleep(0.05)
                except BadRequest:
                    pass

        # Final edit with complete response
        if full_response:
            if is_task_complete(full_response):
                complete_task(chat_id, agent_id=agent_id)
                full_response = full_response.replace("TASK_COMPLETE", "").strip()

            if len(full_response) > 4000:
                await sent.edit_text(full_response[:4000])
                for chunk in [full_response[i:i+4000] for i in range(4000, len(full_response), 4000)]:
                    await message.reply_text(chunk)
            else:
                try:
                    await sent.edit_text(full_response)
                except BadRequest:
                    pass

            save_message(chat_id, agent_id, "assistant", full_response)
        else:
            await sent.edit_text("_(no response)_")

    except Exception as e:
        log.error(f"Streaming error: {e}", exc_info=True)
        await sent.edit_text(f"Error: {str(e)}")

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
    await update.message.reply_text("Context cleared. Ready for a new task.")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", required=True, help="Agent ID (e.g. brad_gant)")
    args = parser.parse_args()

    init_db()

    # Check both GPU instances
    status = both_instances_available()
    log.info(f"GPU status — AMD/Vulkan: {status['amd_vulkan']} | NVIDIA/CUDA: {status['nvidia_cuda']}")

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
