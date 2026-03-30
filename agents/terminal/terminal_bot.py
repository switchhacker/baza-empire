#!/usr/bin/env python3
"""
Baza Terminal Bot
-----------------
Persistent PTY bash session over Telegram.
Input from chat → executed as-is in bash → raw output streamed back.

Security: set TERMINAL_ALLOWED_USERS=<comma-separated telegram user IDs>
          If unset, ALL users are blocked.

Env vars:
    TELEGRAM_TERMINAL         — bot token (required)
    TERMINAL_ALLOWED_USERS    — e.g. "123456789,987654321"
    TERMINAL_WORKING_DIR      — starting directory (default: /)
"""

import os
import sys
import re
import pty
import time
import fcntl
import select
import termios
import struct
import signal
import logging
import threading
import asyncio
from typing import Optional

from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from telegram.error import TelegramError, RetryAfter, BadRequest

# ── Config ────────────────────────────────────────────────────────────────────

TOKEN = os.environ.get("TELEGRAM_TERMINAL", "")

_raw_ids = os.environ.get("TERMINAL_ALLOWED_USERS", "")
ALLOWED_USER_IDS: set[int] = {
    int(x.strip()) for x in _raw_ids.split(",") if x.strip().isdigit()
}

WORKING_DIR = os.environ.get("TERMINAL_WORKING_DIR", "/")

# How often to push output updates to Telegram (seconds)
STREAM_INTERVAL = 0.7

# After this many seconds of no output AND no sentinel, give up waiting
IDLE_TIMEOUT = 120.0

# Max chars per Telegram message
MAX_MSG = 4000

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [terminal] %(levelname)s: %(message)s"
)
log = logging.getLogger("baza.terminal")

# ── ANSI stripping ────────────────────────────────────────────────────────────

_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def clean_output(raw: str) -> str:
    """Strip ANSI codes, normalize line endings, clean up carriage returns."""
    text = strip_ansi(raw)
    # \r\n → \n, lone \r → \n
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


# ── Terminal Session ──────────────────────────────────────────────────────────

class TerminalSession:
    """
    One persistent PTY bash session.
    Thread-safe: a single asyncio.Queue receives chunks from a background reader thread.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop
        self.master_fd: Optional[int] = None
        self._proc_pid: Optional[int] = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._reader: Optional[threading.Thread] = None
        self._dead = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        master_fd, slave_fd = pty.openpty()

        # Terminal attrs on slave: no echo, no ONLCR (\n → \r\n translation off)
        attrs = termios.tcgetattr(slave_fd)
        attrs[3] &= ~(termios.ECHO | termios.ECHOE | termios.ECHOK | termios.ECHONL)
        attrs[1] &= ~termios.ONLCR
        termios.tcsetattr(slave_fd, termios.TCSANOW, attrs)

        # Wide terminal so lines don't wrap unexpectedly
        winsize = struct.pack("HHHH", 50, 220, 0, 0)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)

        import subprocess
        env = {
            **os.environ,
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin:/root/.local/bin:/home/switchhacker/.local/bin",
            "HOME": "/home/switchhacker",
            "TERM": "dumb",
            "COLUMNS": "220",
            "LINES": "50",
            "NO_COLOR": "1",
            "FORCE_COLOR": "0",
            "PS1": "",
            "PS2": "",
            "PS3": "",
            "PS4": "",
        }
        proc = subprocess.Popen(
            ["/bin/bash", "--norc", "--noprofile"],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            cwd=WORKING_DIR,
            env=env,
        )
        os.close(slave_fd)  # parent holds master only

        self._proc_pid = proc.pid
        self.master_fd = master_fd

        # Non-blocking reads on master
        fcntl.fcntl(master_fd, fcntl.F_SETFL, os.O_NONBLOCK)

        # Drain any startup noise
        time.sleep(0.15)
        self._drain_sync()

        # Background reader thread
        self._reader = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader.start()

        log.info(f"PTY session started (pid={proc.pid})")

    def stop(self):
        self._dead = True
        if self._proc_pid:
            try:
                # Kill entire process group so background children die too
                pgid = os.getpgid(self._proc_pid)
                os.killpg(pgid, signal.SIGKILL)
            except OSError:
                try:
                    os.kill(self._proc_pid, signal.SIGKILL)
                except OSError:
                    pass
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = None

    def _drain_sync(self):
        """Discard any buffered output synchronously (called before reader thread starts)."""
        try:
            while True:
                r, _, _ = select.select([self.master_fd], [], [], 0.05)
                if not r:
                    break
                os.read(self.master_fd, 4096)
        except OSError:
            pass

    def _reader_loop(self):
        """Runs in background thread: pushes chunks into asyncio queue."""
        while not self._dead:
            try:
                r, _, _ = select.select([self.master_fd], [], [], 0.1)
                if r:
                    data = os.read(self.master_fd, 4096).decode("utf-8", errors="replace")
                    if data:
                        asyncio.run_coroutine_threadsafe(
                            self._queue.put(data), self._loop
                        )
            except OSError:
                self._dead = True
                break

    # ── Command execution ─────────────────────────────────────────────────────

    def send_raw(self, data: bytes):
        """Write raw bytes to PTY stdin."""
        if self.master_fd is not None and not self._dead:
            os.write(self.master_fd, data)

    def run_command(self, cmd: str) -> str:
        """
        Send a command with a unique sentinel, return sentinel string.
        Caller must watch for the sentinel in output to detect completion.
        """
        sentinel = f"__BAZA_{int(time.monotonic()*10000) % 999999:06d}__"
        # Wrap: run command, then print sentinel regardless of exit code
        wrapped = f"({cmd.rstrip()}); printf '%s\\n' '{sentinel}'\n"
        self.send_raw(wrapped.encode())
        return sentinel

    def clear_queue(self):
        """Discard all queued output (call before issuing a new command)."""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break


# ── Session registry ──────────────────────────────────────────────────────────

class SessionRegistry:
    def __init__(self):
        self._sessions: dict[int, TerminalSession] = {}
        self._passthrough: set[int] = set()   # chats in passthrough mode
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def get(self, chat_id: int) -> TerminalSession:
        if chat_id not in self._sessions or self._sessions[chat_id]._dead:
            sess = TerminalSession(self._loop)
            sess.start()
            self._sessions[chat_id] = sess
        return self._sessions[chat_id]

    def reset(self, chat_id: int):
        if chat_id in self._sessions:
            self._sessions[chat_id].stop()
            del self._sessions[chat_id]
        return self.get(chat_id)


registry = SessionRegistry()


# ── Output helpers ────────────────────────────────────────────────────────────

async def safe_edit(msg, text: str):
    """Edit a Telegram message, ignoring 'not modified' errors."""
    try:
        await msg.edit_text(text)
    except BadRequest as e:
        if "not modified" in str(e).lower():
            pass
        else:
            raise
    except RetryAfter as e:
        await asyncio.sleep(e.retry_after + 0.1)
        await msg.edit_text(text)


def split_chunks(text: str, max_len: int = MAX_MSG) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:max_len])
        text = text[max_len:]
    return chunks


# ── Command handler ───────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    text: str = (update.message.text or "").rstrip("\n")

    if not text:
        return

    # Security
    if not ALLOWED_USER_IDS or user.id not in ALLOWED_USER_IDS:
        await update.message.reply_text("Access denied.")
        return

    sess = registry.get(chat_id)

    # ── Special input sequences ───────────────────────────────────────────────
    # User can send these control codes directly as text

    tl = text.strip().lower()

    if tl == "/start":
        await update.message.reply_text("Baza Terminal ready.")
        return

    if tl in ("reset", "/reset"):
        registry._passthrough.discard(chat_id)
        sess = registry.reset(chat_id)
        await update.message.reply_text("Session reset. Normal mode.")
        return

    if tl in ("/kill", "kill", "^c", "\\x03"):
        if sess._proc_pid:
            try:
                os.killpg(os.getpgid(sess._proc_pid), signal.SIGKILL)
            except OSError:
                sess.send_raw(b"\x03")
        await update.message.reply_text("Killed.")
        return

    if tl in ("/ctrl-z", "^z", "\\x1a"):
        sess.send_raw(b"\x1a")
        return

    if tl in ("/ctrl-d", "^d", "\\x04"):
        sess.send_raw(b"\x04")
        return

    # ── Passthrough mode toggle (for interactive programs like claude CLI) ────
    if tl in ("passthrough", "/passthrough"):
        if chat_id in registry._passthrough:
            registry._passthrough.discard(chat_id)
            await update.message.reply_text("Passthrough OFF — normal shell mode.")
        else:
            registry._passthrough.add(chat_id)
            await update.message.reply_text(
                "Passthrough ON — input sent raw, output streams until quiet.\n"
                "Type 'passthrough' again to return to normal mode."
            )
        return

    passthrough = chat_id in registry._passthrough

    # ── Normalize command case (shell is case-sensitive, user often isn't) ────
    # Lowercase only the first token (the command name), leave args untouched
    parts = text.split(None, 1)
    if parts:
        text = parts[0].lower() + (" " + parts[1] if len(parts) > 1 else "")

    # ── Execute ───────────────────────────────────────────────────────────────

    sess.clear_queue()

    if passthrough:
        # Raw mode: send input directly, stream until output goes quiet
        sess.send_raw((text + "\n").encode())
        sentinel = None
        settle = 2.0   # seconds of silence = response done
    else:
        # Normal mode: sentinel-wrapped command
        sentinel = sess.run_command(text)
        settle = None

    output_buf = ""
    reply_msg = None
    last_push = 0.0
    last_data = time.monotonic()
    done = False

    async def push_update():
        nonlocal reply_msg, last_push
        display = clean_output(output_buf)
        if not display.strip():
            return
        chunks = split_chunks(display)
        if reply_msg is None:
            reply_msg = await update.message.reply_text(chunks[0])
            for c in chunks[1:]:
                reply_msg = await update.message.reply_text(c)
        else:
            if len(chunks) == 1:
                await safe_edit(reply_msg, chunks[0])
            else:
                for c in chunks[:-1]:
                    await update.message.reply_text(c)
                reply_msg = await update.message.reply_text(chunks[-1])
        last_push = time.monotonic()

    while not done:
        try:
            chunk = await asyncio.wait_for(sess._queue.get(), timeout=0.1)
            last_data = time.monotonic()

            if sentinel and sentinel in chunk:
                lines = chunk.split("\n")
                output_buf += "\n".join(l for l in lines if sentinel not in l)
                done = True
            else:
                output_buf += chunk

        except asyncio.TimeoutError:
            pass

        now = time.monotonic()
        if (now - last_push) >= STREAM_INTERVAL and output_buf:
            await push_update()

        # Passthrough: done when output goes quiet for settle seconds
        if passthrough and output_buf and (now - last_data) >= settle:
            done = True

        # Normal fallback timeout
        if not passthrough and not done and (now - last_data) > IDLE_TIMEOUT:
            log.warning(f"Idle timeout for chat {chat_id}")
            done = True

    # Final drain
    await asyncio.sleep(0.15)
    while not sess._queue.empty():
        try:
            chunk = sess._queue.get_nowait()
            if not sentinel or sentinel not in chunk:
                output_buf += chunk
        except asyncio.QueueEmpty:
            break

    await push_update()


# ── Bot setup ─────────────────────────────────────────────────────────────────

async def post_init(app):
    registry.set_loop(asyncio.get_event_loop())
    log.info(f"Terminal bot ready. Allowed users: {ALLOWED_USER_IDS or 'NONE — bot is locked'}")


def main():
    if not TOKEN:
        log.error("TELEGRAM_TERMINAL env var not set — exiting")
        sys.exit(1)

    if not ALLOWED_USER_IDS:
        log.warning(
            "TERMINAL_ALLOWED_USERS is not set — bot will deny all users. "
            "Set it to your Telegram user ID(s) to allow access."
        )

    app = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    # Also handle /commands that aren't our special ones (pass them through as shell input)
    app.add_handler(MessageHandler(filters.COMMAND, handle_message))

    log.info("Starting polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
