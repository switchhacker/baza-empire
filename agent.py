"""
Baza Empire Agent — runs as a single instance per agent.
Usage: python agent.py --agent claw_batto
"""

import os
import sys
import re
import json
import time
import sqlite3
import subprocess
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


FRAMEWORK_DIR = os.path.dirname(os.path.abspath(__file__))
SKILLS_DIR    = os.path.join(FRAMEWORK_DIR, "skills", "shared")
DASHBOARD_DB  = os.path.join(FRAMEWORK_DIR, "dashboard", "baza_projects.db")
VENV_PYTHON   = os.path.join(FRAMEWORK_DIR, "venv", "bin", "python3")

# Per-chat last-upload stash (for follow-up filing commands)
_last_uploads: dict = {}

_FILING_INTENT_RX = re.compile(
    r"\b(attach|file|route|link|save|put|move|assign|log)\b.*\b"
    r"(it|this|that|receipt|permit|coi|w-?9|license|contract|invoice|"
    r"estimate|doc|document|photo|image|file|pdf|blueprint|plan|"
    r"to|as|in|under|for)\b",
    re.IGNORECASE,
)
_FILING_HINT_RX = re.compile(
    r"\b(this (is|was) a|that (is|was) a|it(?:'s| is) (a |an )?)"
    r"(receipt|permit|coi|w-?9|license|contract|invoice|estimate|"
    r"blueprint|change order|lien waiver)\b",
    re.IGNORECASE,
)


def _run_skill(name: str, skill_args: dict, agent_id: str = "") -> dict:
    script = os.path.join(SKILLS_DIR, f"{name}.py")
    if not os.path.exists(script):
        return {"_error": f"skill missing: {name}"}
    env = os.environ.copy()
    env["SKILL_ARGS"] = json.dumps(skill_args)
    if agent_id:
        env["AGENT_ID"] = agent_id
    try:
        out = subprocess.check_output(
            [VENV_PYTHON, script], env=env, timeout=240,
            stderr=subprocess.STDOUT, cwd=FRAMEWORK_DIR,
        )
        txt = out.decode("utf-8", errors="ignore").strip()
        try:
            return json.loads(txt)
        except Exception:
            return {"_raw": txt[:2000]}
    except subprocess.CalledProcessError as e:
        return {"_error": f"exit {e.returncode}", "_raw": e.output.decode(errors="ignore")[:1000]}
    except Exception as e:
        return {"_error": str(e)}


def _load_curated_analysis(file_path: str) -> dict:
    try:
        conn = sqlite3.connect(DASHBOARD_DB)
        conn.row_factory = sqlite3.Row
        r = conn.execute(
            "SELECT doc_type, entity, doc_date, summary, relevance, tags, "
            "       suggested_name, confidence, content_text, project_id "
            "FROM ahb_documents WHERE file_path=?", (file_path,)
        ).fetchone()
        conn.close()
        if not r:
            return {}
        out = dict(r)
        try:
            out["tags"] = json.loads(out.get("tags") or "[]")
        except Exception:
            out["tags"] = []
        return out
    except Exception:
        return {}


def _format_filing_reply(filed: dict) -> str | None:
    action = filed.get("action")
    if action == "filed_receipt":
        return (f"\U0001f9fe Receipt filed in AHB123\n"
                f"Vendor: {filed.get('vendor') or '-'}\n"
                f"Date: {filed.get('receipt_date') or '-'}\n"
                f"Total: ${float(filed.get('total') or 0):.2f}\n"
                f"Project: {filed.get('project_id') or '-'}\n"
                f"ID: {filed.get('receipt_id','')}")
    elif action == "linked_to_project":
        return (f"\U0001f5c2 Attached to project {filed.get('project_id')}\n"
                f"({filed.get('project_note','')})")
    elif action == "unassigned":
        return (f"\U0001f4ce Stored in Document Library (no project match)\n"
                f"hint: {filed.get('hint') or 'none'} \u2014 {filed.get('reason','')}")
    elif action == "kept_in_library":
        return None
    elif filed.get("success") is False:
        return f"\u26a0\ufe0f Filing error: {filed.get('error') or filed}"
    return None


async def handle_attachment(
    update: Update, context: ContextTypes.DEFAULT_TYPE, agent: dict
):
    """Save every photo/document/video/audio, curate, then auto-file."""
    import datetime as _dt
    msg = update.message
    if not msg:
        return
    chat_id = str(msg.chat_id)
    agent_id = agent["id"]
    caption = (msg.caption or "").strip()

    upload_dir = os.path.join(FRAMEWORK_DIR, "dashboard", "artifacts",
                              f"{agent_id}-uploads")
    os.makedirs(upload_dir, exist_ok=True)

    file_obj = orig_name = kind = None
    if msg.photo:
        file_obj = await context.bot.get_file(msg.photo[-1].file_id)
        orig_name = f"photo_{msg.photo[-1].file_unique_id}.jpg"; kind = "photo"
    elif msg.document:
        file_obj = await context.bot.get_file(msg.document.file_id)
        orig_name = msg.document.file_name or f"doc_{msg.document.file_unique_id}"; kind = "document"
    elif msg.video:
        file_obj = await context.bot.get_file(msg.video.file_id)
        orig_name = msg.video.file_name or f"video_{msg.video.file_unique_id}.mp4"; kind = "video"
    elif msg.audio:
        file_obj = await context.bot.get_file(msg.audio.file_id)
        orig_name = msg.audio.file_name or f"audio_{msg.audio.file_unique_id}.mp3"; kind = "audio"
    elif msg.voice:
        file_obj = await context.bot.get_file(msg.voice.file_id)
        orig_name = f"voice_{msg.voice.file_unique_id}.ogg"; kind = "voice"
    if not file_obj:
        return

    safe = re.sub(r"[^\w.\-_ ()]", "_", orig_name).strip() or "upload"
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"{ts}_{safe}"
    fpath = os.path.join(upload_dir, fname)
    await file_obj.download_to_drive(fpath)

    # Sidecar meta
    try:
        with open(fpath + ".meta", "w") as mf:
            mf.write(f"agent_id={agent_id}\nchat_id={chat_id}\nkind={kind}\n"
                     f"received_at={_dt.datetime.now().isoformat()}\n")
            if caption:
                mf.write(f"caption={caption[:500]}\n")
    except Exception:
        pass

    log.info(f"[{agent_id}] saved {kind}: {fname}")
    _last_uploads[chat_id] = {"file_path": fpath, "kind": kind, "ts": time.time()}
    await context.bot.send_message(chat_id=chat_id,
        text=f"\u2705 Got your {kind}: {fname}\n\u2026 analyzing...")

    # Background: curate then file
    async def _curate_and_file():
        try:
            loop = asyncio.get_event_loop()
            curate_result = await loop.run_in_executor(
                EXECUTOR, _run_skill, "curate_document",
                {"file_path": fpath, "agent_id": agent_id, "chat_id": chat_id},
                agent_id,
            )
            analysis = curate_result if isinstance(curate_result, dict) else {}

            doc_type = (analysis.get("doc_type") or "document").upper()
            entity   = analysis.get("entity") or "unknown"
            summary  = (analysis.get("summary") or "").strip()
            suggested = analysis.get("suggested_name") or fname
            tags     = analysis.get("tags") or []

            out = f"\u2705 Filed: {doc_type}\nEntity: {entity}\n"
            if analysis.get("doc_date"):
                out += f"Date: {analysis['doc_date']}\n"
            if summary:
                out += f"\n{summary}\n"
            if tags:
                out += f"\ntags: {', '.join(tags[:6])}\n"
            out += f"\n\U0001f4c1 {suggested}"
            await context.bot.send_message(chat_id=chat_id, text=out)

            # Post-curate filing
            file_result = await loop.run_in_executor(
                EXECUTOR, _run_skill, "file_document",
                {"file_path": fpath, "analysis": analysis, "caption": caption,
                 "agent_id": agent_id},
                agent_id,
            )
            filed = file_result if isinstance(file_result, dict) else {}
            reply = _format_filing_reply(filed)
            if reply:
                await context.bot.send_message(chat_id=chat_id, text=reply)
            # Log to activity feed
            _log_agent_activity(agent_id, filed, analysis, caption)
        except Exception as e:
            log.error(f"[{agent_id}] curate/file failed: {e}")

    asyncio.ensure_future(_curate_and_file())


async def handle_filing_followup(
    update: Update, context: ContextTypes.DEFAULT_TYPE, agent: dict
) -> bool:
    """If user sends a filing command after a recent upload, re-file it."""
    msg = update.message
    if not msg or not msg.text:
        return False
    text = msg.text
    chat_id = str(msg.chat_id)

    if not (_FILING_INTENT_RX.search(text) or _FILING_HINT_RX.search(text)):
        return False

    upload = _last_uploads.get(chat_id)
    if not upload or (time.time() - upload.get("ts", 0)) > 900:
        return False
    fpath = upload.get("file_path", "")
    if not fpath or not os.path.exists(fpath):
        return False

    analysis = _load_curated_analysis(fpath)

    # Extract project name from message
    m = re.search(r"\b(?:to|for|under|in)\s+([A-Za-z0-9][\w\s\-\.]{2,40}?)"
                  r"(?:\s+project|\s*[,.?!]|\s*$)", text, re.IGNORECASE)
    if m:
        analysis["project_hint"] = m.group(1).strip()

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            EXECUTOR, _run_skill, "file_document",
            {"file_path": fpath, "analysis": analysis, "caption": text,
             "agent_id": agent["id"]},
            agent["id"],
        )
        filed = result if isinstance(result, dict) else {}
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"Filing failed: {e}")
        return True

    reply = _format_filing_reply(filed) or f"Filed as {filed.get('final_doc_type','document')}."
    save_message(int(chat_id), agent["id"], "user", text)
    save_message(int(chat_id), agent["id"], "assistant", reply)
    await context.bot.send_message(chat_id=chat_id, text=reply)
    _log_agent_activity(agent["id"], filed, analysis, text)
    return True


async def _text_with_filing(update, context, agent):
    """Wrapper: try filing followup first, then fall through to normal message."""
    if await handle_filing_followup(update, context, agent):
        return
    if _is_print_request(update.message.text or ""):
        await handle_print(update, context, agent)
    else:
        await handle_message(update, context, agent)


def _log_agent_activity(agent_id, filed, analysis, caption=""):
    """Log a filing action to the activity feed."""
    try:
        from core.context_db import journal_log
        action = filed.get("action", "filed")
        doc_type = (filed.get("final_doc_type") or analysis.get("doc_type") or "document").upper()
        entity = analysis.get("entity") or ""
        proj = filed.get("project_id") or ""
        agent_names = {"phil_hass":"Phil","claw_batto":"Claw","simon_bately":"Simon",
                       "sam_axe":"Sam","duke_harmon":"Duke","rex_valor":"Rex",
                       "scout_reeves":"Scout","nova_sterling":"Nova"}
        name = agent_names.get(agent_id, agent_id)
        if action == "filed_receipt":
            vendor = filed.get("vendor") or entity
            total = filed.get("total") or 0
            summary = f"{name} filed receipt from {vendor} (${float(total):.2f}) to {proj}"
        elif action == "linked_to_project":
            summary = f"{name} attached {doc_type} ({entity}) to project {proj}"
        elif action == "unassigned":
            summary = f"{name} filed {doc_type} ({entity}) — no project matched"
        else:
            summary = f"{name} processed {doc_type} ({entity})"
        journal_log(
            agent_id=agent_id, task_type="document_filed",
            task_description=summary, action_summary=summary,
            requested_by="serge", status="completed",
        )
    except Exception as e:
        log.warning(f"activity log failed: {e}")


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

    # Attachment handler — photos, docs, video, audio, voice
    app.add_handler(MessageHandler(
        filters.PHOTO | filters.Document.ALL | filters.VIDEO | filters.AUDIO | filters.VOICE,
        lambda u, c: handle_attachment(u, c, agent)
    ))

    # Text handler — filing followup > print intercept > normal message
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        lambda u, c: _text_with_filing(u, c, agent)
    ))

    # Heartbeat via daemon thread — fires every 60 seconds
    start_heartbeat_thread(agent)

    log.info(f"{agent['name']} is listening...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
