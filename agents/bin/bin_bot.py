#!/usr/bin/env python3
"""
Baza Bin Bot
------------
Send ANY file to this Telegram bot → it lands in the Baza Bin (Data Hub) and can
be pulled into projects, email, social, and the image picker from the dashboard.

Replaces the old Terminal shell bot. NO command execution.

Env vars (reused from the old terminal bot):
    TELEGRAM_TERMINAL       — bot token (required)
    TERMINAL_ALLOWED_USERS  — comma-separated Telegram user IDs (empty ⇒ deny all)
"""
import os
import sys
import logging
import tempfile

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if FRAMEWORK_DIR not in sys.path:
    sys.path.insert(0, FRAMEWORK_DIR)

from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

from dashboard import bin_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bin_bot")

TOKEN = os.environ.get("TELEGRAM_TERMINAL", "")
_raw_ids = os.environ.get("TERMINAL_ALLOWED_USERS", "")
ALLOWED_USER_IDS = {int(x.strip()) for x in _raw_ids.split(",") if x.strip().isdigit()}

# Telegram Bot API caps getFile downloads at ~20 MB.
TELEGRAM_MAX_BYTES = 20 * 1024 * 1024


def _pick_file(msg):
    """Return (file_id, filename, declared_size) for whatever file the message
    carries, or (None, None, 0)."""
    if getattr(msg, "photo", None):
        p = msg.photo[-1]
        return p.file_id, f"photo_{p.file_unique_id}.jpg", getattr(p, "file_size", 0) or 0
    if getattr(msg, "document", None):
        d = msg.document
        return d.file_id, (d.file_name or f"doc_{d.file_unique_id}"), getattr(d, "file_size", 0) or 0
    if getattr(msg, "video", None):
        v = msg.video
        return v.file_id, (getattr(v, "file_name", None) or f"video_{v.file_unique_id}.mp4"), getattr(v, "file_size", 0) or 0
    if getattr(msg, "audio", None):
        a = msg.audio
        return a.file_id, (getattr(a, "file_name", None) or f"audio_{a.file_unique_id}.mp3"), getattr(a, "file_size", 0) or 0
    if getattr(msg, "voice", None):
        vo = msg.voice
        return vo.file_id, f"voice_{vo.file_unique_id}.ogg", getattr(vo, "file_size", 0) or 0
    return None, None, 0


def save_incoming(msg, *, get_file, tg_user_id):
    """Core, testable handler. `get_file(file_id, dest_path)` downloads to disk.
    Returns the bin item dict, or None if there's no file or it's over the
    Telegram 20 MB limit."""
    file_id, filename, size = _pick_file(msg)
    if not file_id:
        return None
    if size and size > TELEGRAM_MAX_BYTES:
        return None
    caption = (getattr(msg, "caption", "") or "").strip()
    with tempfile.TemporaryDirectory() as td:
        tmp = os.path.join(td, "download.bin")
        get_file(file_id, tmp)
        if os.path.getsize(tmp) > TELEGRAM_MAX_BYTES:
            return None
        return bin_store.add_file(filename=filename, src_path=tmp, caption=caption,
                                  source="telegram", tg_user_id=tg_user_id)


def _allowed(update: Update) -> bool:
    u = update.effective_user
    return bool(u and u.id in ALLOWED_USER_IDS)


async def on_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    msg = update.message
    if not msg:
        return
    file_id, filename, size = _pick_file(msg)
    if not file_id:
        await msg.reply_text("Send me any file (photo, document, video, audio) and "
                             "it lands in your Baza Bin. Commands: /count, /list")
        return
    if size and size > TELEGRAM_MAX_BYTES:
        await msg.reply_text("⚠️ That file is over Telegram's 20 MB bot limit. "
                             "Drop it via the Data Hub → Bin upload box instead.")
        return
    caption = (msg.caption or "").strip()
    with tempfile.TemporaryDirectory() as td:
        tmp = os.path.join(td, "dl.bin")
        tf = await context.bot.get_file(file_id)
        await tf.download_to_drive(tmp)
        if os.path.getsize(tmp) > TELEGRAM_MAX_BYTES:
            await msg.reply_text("⚠️ Over Telegram's 20 MB limit — use the Data Hub upload box.")
            return
        item = bin_store.add_file(filename=filename, src_path=tmp, caption=caption,
                                  source="telegram", tg_user_id=update.effective_user.id)
    n = len(bin_store.list_items(limit=500))
    await msg.reply_text(f"✅ In the bin ({n} item{'s' if n != 1 else ''}): {item['name']}")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    await update.message.reply_text(
        "📦 Baza Bin. Send me any file and it lands in the bin (Data Hub → Bin), "
        "ready to pull into projects, email, or social. Commands: /count, /list")


async def cmd_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    n = len(bin_store.list_items(limit=500))
    await update.message.reply_text(f"📦 {n} item(s) in the bin.")


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    items = bin_store.list_items(limit=10)
    if not items:
        await update.message.reply_text("Bin is empty.")
        return
    lines = [f"• {i['name']} ({i['kind']}, {i['size']} B)" for i in items]
    await update.message.reply_text("Latest in the bin:\n" + "\n".join(lines))


def main():
    if not TOKEN:
        log.error("TELEGRAM_TERMINAL env var not set — exiting")
        sys.exit(1)
    if not ALLOWED_USER_IDS:
        log.warning("TERMINAL_ALLOWED_USERS not set — bot denies all users.")
    bin_store.init_bin_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("count", cmd_count))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(MessageHandler(
        filters.PHOTO | filters.Document.ALL | filters.VIDEO | filters.AUDIO | filters.VOICE,
        on_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    log.info("Baza Bin bot starting. Allowed: %s", ALLOWED_USER_IDS or "NONE (locked)")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
