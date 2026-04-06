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
from concurrent.futures import ThreadPoolExecutor
from telegram import Update
from telegram.ext import (
    Application, MessageHandler, CommandHandler,
    filters, ContextTypes
)
from telegram.error import BadRequest, TimedOut
from core.ollama_client import chat_stream_pooled, both_instances_available
from core.memory import (
    init_db, save_message, get_history,
    get_active_task, set_task, complete_task
)
from core.coordinator import should_agent_respond, build_group_context, is_task_complete

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
)
log = logging.getLogger(__name__)

CONTEXT_LIMIT    = 40   # messages pulled from DB
HISTORY_IN_PROMPT = 20  # messages passed to model
SNAP_THRESHOLD   = 60   # chars — short messages get snap mode

# Dedicated thread pool for inference (non-blocking)
EXECUTOR = ThreadPoolExecutor(max_workers=4)

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


def is_snap(text: str) -> bool:
    return len(text.strip()) < SNAP_THRESHOLD and "\n" not in text.strip()


def snap_suffix() -> str:
    return (
        "\n\nIMPORTANT: This is a short/simple message. "
        "Reply in 1-3 sentences max. Be direct and punchy. No lists, no padding."
    )


def run_inference(agent: dict, messages: list) -> str:
    """Blocking inference — runs in thread pool."""
    full = ""
    for chunk in chat_stream_pooled(
        model=agent["model"],
        messages=messages,
        system_prompt=agent["system_prompt"],
        agent_id=agent["id"]
    ):
        full += chunk
    return full


async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    agent: dict
):
    message = update.message
    if not message or not message.text:
        return

    chat_id   = message.chat_id
    user_text = message.text
    is_group  = message.chat.type in ["group", "supergroup"]
    agent_id  = agent["id"]

    # Group chat routing
    if is_group:
        if message.from_user and message.from_user.is_bot:
            return
        if not should_agent_respond(agent_id, user_text, is_group=True):
            return

    log.info(f"[{agent['name']}] chat={chat_id} snap={is_snap(user_text)} | {user_text[:80]}")

    # Build prompt
    history     = get_history(chat_id, agent_id=agent_id, limit=CONTEXT_LIMIT)
    current_task = get_active_task(chat_id, agent_id=agent_id)

    if not current_task:
        set_task(chat_id, user_text[:500], agent_id=agent_id)

    messages = []

    if is_group and history:
        ctx = build_group_context(history, current_task)
        messages.append({"role": "user",      "content": f"[Context]\n{ctx}"})
        messages.append({"role": "assistant", "content": "Got it."})

    for msg in history[-HISTORY_IN_PROMPT:]:
        role = "assistant" if msg.get("agent") == agent_id else "user"
        messages.append({"role": role, "content": msg["content"]})

    final_text = user_text + (snap_suffix() if is_snap(user_text) else "")
    messages.append({"role": "user", "content": final_text})

    save_message(chat_id, agent_id, "user", user_text)

    # Show typing indicator and placeholder
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        sent = await message.reply_text("✍️")
    except Exception as e:
        log.warning(f"Could not send placeholder: {e}")
        sent = None

    # Run inference in thread pool — does NOT block the event loop
    loop = asyncio.get_event_loop()
    try:
        full_response = await loop.run_in_executor(
            EXECUTOR,
            run_inference,
            agent,
            messages
        )
    except Exception as e:
        log.error(f"Inference failed: {e}", exc_info=True)
        if sent:
            try:
                await sent.edit_text(f"⚠️ {str(e)}")
            except Exception:
                pass
        return

    if not full_response:
        if sent:
            await sent.edit_text("_(no response)_")
        return

    # Check task completion signal
    if is_task_complete(full_response):
        complete_task(chat_id, agent_id=agent_id)
        full_response = full_response.upper().replace("TASK_COMPLETE", "")
        full_response = full_response.strip()

    save_message(chat_id, agent_id, "assistant", full_response)

    # Deliver response (split if >4000 chars)
    chunks = [full_response[i:i+4000] for i in range(0, len(full_response), 4000)]
    try:
        if sent:
            await sent.edit_text(chunks[0])
        else:
            await message.reply_text(chunks[0])
    except (BadRequest, TimedOut):
        await message.reply_text(chunks[0])

    for extra in chunks[1:]:
        await asyncio.sleep(0.3)
        await message.reply_text(extra)


async def handle_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    agent: dict
):
    await update.message.reply_text(
        f"👋 {agent['name']} online.\n{agent['role']}\nModel: {agent['model']}"
    )


async def handle_reset(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    agent: dict
):
    chat_id  = update.message.chat_id
    agent_id = agent["id"]
    complete_task(chat_id, agent_id=agent_id)
    try:
        from core.memory import get_conn
        conn = get_conn()
        cur  = conn.cursor()
        cur.execute(
            "DELETE FROM messages WHERE chat_id = %s AND agent_id = %s",
            (chat_id, agent_id)
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        log.error(f"Reset error: {e}")
    await update.message.reply_text("Context cleared. Ready.")


def _is_print_request(text: str) -> bool:
    t = text.lower().strip()
    phrases = ["print this", "print that", "print it", "print the",
               "print a test", "print test", "send to printer",
               "print page", "printer status", "print status",
               "print queue", "cancel print", "print last",
               "print image", "print photo", "print file",
               "print invoice", "print contract", "print report",
               "print document", "print pdf"]
    return any(p in t for p in phrases) or t == "print"


async def handle_print(update: Update, context: ContextTypes.DEFAULT_TYPE, agent: dict):
    """Intercept print requests — run skill directly, skip LLM."""
    import json as _json, subprocess, time as _time
    text = (update.message.text or "").lower().strip()
    agent_id = agent["id"]
    skill_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills", "shared", "print_document.py")
    venv_python = os.path.join(os.path.dirname(os.path.abspath(__file__)), "venv", "bin", "python3")

    def _run_skill(args):
        env = os.environ.copy()
        env["SKILL_ARGS"] = _json.dumps(args)
        env["AGENT_ID"] = agent_id
        r = subprocess.run([venv_python, skill_path], capture_output=True, text=True, timeout=30, env=env)
        return r.stdout.strip(), r.returncode == 0

    await update.message.reply_text("Sending to printer...")

    if "status" in text or "queue" in text:
        out, ok = _run_skill({"action": "status"})
        try:
            parsed = _json.loads(out.split("\n")[-1])
            reply = f"Printer: {parsed.get('printer', '—')}\nStatus: {parsed.get('status', '—')}\nJobs: {parsed.get('pending_jobs', 0)}"
        except Exception:
            reply = out or "Could not check printer"
    elif "cancel" in text:
        out, ok = _run_skill({"action": "cancel"})
        reply = "Print jobs cancelled." if ok else f"Cancel failed: {out}"
    elif "test" in text:
        out, ok = _run_skill({"text": f"BAZA EMPIRE — PRINT TEST\n\nHP Smart Tank 5101 online.\nPrinted by: {agent['name']}", "title": "Baza Empire Test Page"})
        reply = "Test page sent to printer." if ok else f"Print failed: {out}"
    else:
        # Try to find a file to print — search artifacts
        import glob as _glob
        file_to_print = None
        artifacts_base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard", "artifacts")

        # Extract target noun from message
        t = text
        for prefix in ["print the ", "print my ", "print this ", "print that ",
                        "print last ", "print a ", "print "]:
            if t.startswith(prefix):
                target = t[len(prefix):].strip().rstrip(".")
                if target and target not in ("this", "that", "it", "page", "file", "document"):
                    matches = _glob.glob(os.path.join(artifacts_base, "**", f"*{target}*"), recursive=True)
                    matches = [m for m in matches if not m.endswith(".meta") and os.path.isfile(m)]
                    if matches:
                        file_to_print = max(matches, key=os.path.getmtime)
                break

        # "print last image/photo" — find most recent image
        if not file_to_print and any(w in t for w in ["image", "photo", "picture", "last"]):
            imgs = []
            for ext in ["*.png", "*.jpg", "*.jpeg"]:
                imgs.extend(_glob.glob(os.path.join(artifacts_base, "**", ext), recursive=True))
            imgs = [i for i in imgs if not i.endswith(".meta")]
            if imgs:
                file_to_print = max(imgs, key=os.path.getmtime)

        if file_to_print:
            out, ok = _run_skill({"file_path": file_to_print})
            reply = f"Sent to printer: {os.path.basename(file_to_print)}" if ok else f"Print failed: {out}"
        else:
            reply = "Nothing to print. Send a photo, specify a filename, or say 'print test page'."

    save_message(update.message.chat_id, agent_id, "user", update.message.text)
    save_message(update.message.chat_id, agent_id, "assistant", reply)
    await update.message.reply_text(reply)


def start_heartbeat_thread(agent: dict):
    """Start a daemon thread that emits Redis heartbeat every 60s."""
    import threading, time as _time
    def _loop():
        try:
            import redis, json as _json
            r = redis.Redis(host='localhost', port=6379, decode_responses=True)
        except Exception as e:
            log.warning(f"Heartbeat init failed (no redis?): {e}")
            return
        while True:
            try:
                payload = _json.dumps({
                    "agent_id": agent["id"],
                    "model": agent["model"],
                    "status": "idle",
                    "ts": int(_time.time()),
                })
                r.setex(f"baza:heartbeat:{agent['id']}", 120, payload)
            except Exception:
                pass
            _time.sleep(60)
    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    log.info(f"Heartbeat thread started for {agent['name']}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", required=True)
    args = parser.parse_args()

    init_db()

    status = both_instances_available()
    log.info(
        f"GPU status — AMD:11434={status['amd_vulkan']} | "
        f"NVIDIA:11435={status['nvidia_cuda']}"
    )
    if not status["amd_vulkan"] and not status["nvidia_cuda"]:
        log.error("No Ollama instances available.")
        sys.exit(1)

    agent = load_agent(args.agent)
    log.info(f"Starting agent: {agent['name']} | model: {agent['model']}")

    app = Application.builder().token(agent["token"]).build()
    app.add_handler(CommandHandler("start", lambda u, c: handle_start(u, c, agent)))
    app.add_handler(CommandHandler("reset", lambda u, c: handle_reset(u, c, agent)))

    # Print intercept — must be before general message handler
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.Regex(r'(?i)print|printer'),
        lambda u, c: handle_print(u, c, agent) if _is_print_request(u.message.text or "") else handle_message(u, c, agent)
    ))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        lambda u, c: handle_message(u, c, agent)
    ))

    # Heartbeat via daemon thread — fires every 60 seconds
    start_heartbeat_thread(agent)

    log.info(f"{agent['name']} is listening...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
