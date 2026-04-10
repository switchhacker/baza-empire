#!/usr/bin/env python3
"""
Specter Voss — Telegram → OpenClaw Bridge
Receives Telegram messages and routes them through OpenClaw (which uses
Ollama cloud models with the Specter persona) — Option B architecture.

This is a thin python wrapper that:
  1. Polls Telegram for new messages
  2. Forwards them to `openclaw agent --local --session-id <chat_id> --message <text>`
  3. Sends the OpenClaw reply back via Telegram
  4. Logs to baza's PostgreSQL task_journal
"""
import os
import json
import time
import socket
import logging
import subprocess
import urllib.request
import urllib.parse
from datetime import datetime

# Prefer IPv6 for Telegram (avoids some NAT/CGNAT paths) but DO NOT force IPv6-only —
# Tailscale (100.x) and most local services are IPv4. Falling back to original on empty results.
_orig_getaddrinfo = socket.getaddrinfo
def _prefer_v6(host, *a, **k):
    res = _orig_getaddrinfo(host, *a, **k)
    if not res: return res
    # If this is a Tailscale or RFC1918 host, return as-is (IPv4 path)
    if isinstance(host, str) and (host.startswith("100.") or host.startswith("10.") or host.startswith("192.168.") or host == "localhost" or host.startswith("127.")):
        return res
    v6 = [r for r in res if r[0] == socket.AF_INET6]
    return v6 or res
socket.getaddrinfo = _prefer_v6

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger("specter.bridge")

# ── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("TELEGRAM_SPECTER_VOSS", "")
SERGE_CHAT_ID = os.environ.get("SERGE_CHAT_ID", "")
OPENCLAW_BIN = "/usr/bin/openclaw"
OLLAMA_API_KEY = os.environ.get("OLLAMA_API_KEY", "")
DB_HOST = os.environ.get("BAZA_DB_HOST", "100.127.118.103")
DB_USER = os.environ.get("BAZA_DB_USER", "switchhacker")
DB_NAME = os.environ.get("BAZA_DB_NAME", "baza_agents")
DB_PASS = os.environ.get("DB_PASSWORD", "baza2026")

if not BOT_TOKEN:
    logger.error("TELEGRAM_SPECTER_VOSS env var not set")
    exit(1)

API = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ── Telegram API ──────────────────────────────────────────────────────────────
def tg_request(method, params=None, timeout=30):
    url = f"{API}/{method}"
    if params:
        data = json.dumps(params).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    else:
        req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        logger.error(f"Telegram API error ({method}): {e}")
        return {"ok": False, "error": str(e)}


def get_updates(offset=0, timeout=25):
    return tg_request("getUpdates", {"offset": offset, "timeout": timeout, "allowed_updates": ["message"]}, timeout=timeout + 5)


def md_to_html(text):
    """Convert simple markdown to Telegram HTML."""
    import re
    # Escape HTML special chars first
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Code blocks (```...```)
    text = re.sub(r'```(\w*)\n?(.*?)```', lambda m: f'<pre>{m.group(2)}</pre>', text, flags=re.DOTALL)
    # Inline code (`...`)
    text = re.sub(r'`([^`\n]+)`', r'<code>\1</code>', text)
    # Bold (**text** or __text__)
    text = re.sub(r'\*\*([^*\n]+)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__([^_\n]+)__', r'<b>\1</b>', text)
    # Italic (*text* or _text_) — only single, not adjacent to **
    text = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'<i>\1</i>', text)
    # Strip markdown headers (# Header → bold)
    text = re.sub(r'^#{1,6}\s+(.+)$', r'<b>\1</b>', text, flags=re.MULTILINE)
    # Convert markdown tables to clean lists (Telegram doesn't render tables)
    lines = text.split('\n')
    out_lines = []
    in_table = False
    table_headers = []
    for line in lines:
        if re.match(r'^\s*\|.*\|\s*$', line):
            if re.match(r'^\s*\|[\s\-:|]+\|\s*$', line):
                in_table = True
                continue
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            if not in_table and not table_headers:
                table_headers = cells
                continue
            if table_headers and len(cells) == len(table_headers):
                row = ' • '.join(f"<b>{h}:</b> {c}" for h, c in zip(table_headers, cells) if c)
                out_lines.append(f"  • {row}")
            else:
                out_lines.append('  • ' + ' · '.join(cells))
        else:
            if table_headers:
                table_headers = []
                in_table = False
            out_lines.append(line)
    return '\n'.join(out_lines)


def send_message(chat_id, text, parse_mode="HTML"):
    if parse_mode == "HTML":
        text = md_to_html(text)
    params = {"chat_id": chat_id, "text": text[:4000]}
    if parse_mode:
        params["parse_mode"] = parse_mode
    result = tg_request("sendMessage", params)
    # If HTML parsing failed, retry as plain text
    if not result.get("ok") and parse_mode == "HTML":
        logger.warning(f"HTML send failed, retrying plain: {result.get('description','')[:200]}")
        # Strip HTML tags
        import re
        plain = re.sub(r'<[^>]+>', '', text)
        params = {"chat_id": chat_id, "text": plain[:4000]}
        result = tg_request("sendMessage", params)
    return result


def send_chat_action(chat_id, action="typing"):
    return tg_request("sendChatAction", {"chat_id": chat_id, "action": action})


# ── OpenClaw Agent Call ───────────────────────────────────────────────────────
RATE_LIMIT_MARKERS = (
    "rate limit reached",
    "rate-limit",
    "429",
    "quota exceeded",
    "too many requests",
    "rate_limit_exceeded",
)

LOCAL_FALLBACK_MODEL = os.environ.get("SPECTER_LOCAL_FALLBACK", "qwen2.5:14b")
# NUC's local Ollama only hosts cloud models — fall back to baza server (Tailscale)
# where the real local models (qwen2.5:14b, mistral-small:22b, etc.) live on GPUs.
LOCAL_OLLAMA_URL    = os.environ.get("LOCAL_OLLAMA_URL", "http://100.127.118.103:11434")
SPECTER_PERSONA = (
    "You are Specter Voss — Ghost Operative for Baza Empire, Serge Tkach's "
    "intelligence agent. You're sharp, direct, and helpful. When the cloud "
    "model is rate-limited, you fall back to a local model and tell Serge "
    "you're running on a backup brain so he knows responses may be terser. "
    "First person always. Never break character."
)


def _looks_rate_limited(text):
    if not text:
        return False
    low = text.lower()
    return any(m in low for m in RATE_LIMIT_MARKERS)


def call_local_fallback(session_id, message):
    """When the cloud is rate-limited, run OpenClaw with the specter-local agent
    which uses baza's GPU via openai-compat. This gives FULL agentic behavior
    (tools, multi-turn, search, shell) — same as the cloud path, just a smaller model.
    Falls back to a direct Ollama one-shot only if OpenClaw itself fails."""
    env = os.environ.copy()
    env["OLLAMA_API_KEY"] = OLLAMA_API_KEY or "ollama-local"
    env["OPENAI_API_KEY"] = "ollama-local"
    env["GODEBUG"] = "netdns=go+4"

    try:
        logger.info("local fallback: running OpenClaw specter-local (full agentic)")
        proc = subprocess.run(
            [
                OPENCLAW_BIN, "agent",
                "--local",
                "--agent", "specter-local",
                "--session-id", str(session_id),
                "--message", message,
                "--timeout", "240",
            ],
            capture_output=True,
            text=True,
            timeout=260,
            env=env,
        )
        reply = proc.stdout.strip()
        # Strip ANSI codes
        import re
        reply = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', reply)
        # Strip any leaked <think>...</think>
        reply = re.sub(r'<think>.*?</think>\s*', '', reply, flags=re.DOTALL).strip()

        if reply and not _looks_rate_limited(reply):
            return f"👻 _(cloud rate-limited — running on local {LOCAL_FALLBACK_MODEL} via OpenClaw)_\n\n{reply}"

        # If OpenClaw also returned empty/rate-limited, fall through to one-shot
        logger.warning(f"local openclaw returned empty or rate-limited, trying direct Ollama")
    except subprocess.TimeoutExpired:
        logger.warning("local openclaw timed out after 260s, trying direct Ollama")
    except Exception as e:
        logger.warning(f"local openclaw failed ({e}), trying direct Ollama")

    # Last resort: direct one-shot to baza Ollama (no tools, just conversation)
    try:
        payload = json.dumps({
            "model": LOCAL_FALLBACK_MODEL,
            "stream": False,
            "messages": [
                {"role": "system", "content": SPECTER_PERSONA},
                {"role": "user",   "content": message},
            ],
            "options": {"temperature": 0.7, "num_predict": 1500, "num_ctx": 8192},
        }).encode()
        req = urllib.request.Request(
            f"{LOCAL_OLLAMA_URL}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read())
        import re
        reply = (data.get("message", {}).get("content") or "").strip()
        reply = re.sub(r'<think>.*?</think>\s*', '', reply, flags=re.DOTALL).strip()
        if reply:
            return f"👻 _(cloud rate-limited — running on local {LOCAL_FALLBACK_MODEL}, reduced mode)_\n\n{reply}"
        return "_(local fallback returned empty — both cloud and local failed)_"
    except Exception as e:
        logger.error(f"local direct Ollama also failed: {e}")
        return f"_(cloud rate-limited AND local fallback failed: {e})_"


def call_openclaw(session_id, message):
    """Call OpenClaw agent with Specter persona, return reply text.
    On rate limit / quota exhaustion, transparently fall back to local Ollama."""
    env = os.environ.copy()
    env["OLLAMA_API_KEY"] = OLLAMA_API_KEY
    env["GODEBUG"] = "netdns=go+4"

    try:
        proc = subprocess.run(
            [
                OPENCLAW_BIN, "agent",
                "--local",
                "--session-id", str(session_id),
                "--message", message
            ],
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
        )
        if proc.returncode != 0:
            logger.error(f"OpenClaw error: {proc.stderr[:500]}")
            # If subprocess failed AND it looks like rate-limit, fall back
            combined = (proc.stderr or "") + (proc.stdout or "")
            if _looks_rate_limited(combined):
                logger.info(f"OpenClaw rate-limited (returncode={proc.returncode}), falling back to local Ollama")
                return call_local_fallback(session_id, message)
            return f"_(OpenClaw error: {proc.stderr.strip()[:200]})_"

        reply = proc.stdout.strip()
        # Strip ANSI codes if any
        import re
        reply = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', reply)

        # Detect rate-limit messages in the SUCCESSFUL stdout (openclaw passes them through)
        if _looks_rate_limited(reply):
            logger.info("OpenClaw returned rate-limit message in stdout, falling back to local Ollama")
            return call_local_fallback(session_id, message)

        return reply or "_(empty response)_"
    except subprocess.TimeoutExpired:
        return "_(response timed out after 3 minutes)_"
    except Exception as e:
        logger.error(f"call_openclaw exception: {e}")
        return f"_(error: {e})_"


# ── Database journal logging ──────────────────────────────────────────────────
def journal_log(chat_id, role, content, success=True):
    try:
        import psycopg2
        conn = psycopg2.connect(host=DB_HOST, port=5432, dbname=DB_NAME, user=DB_USER, password=DB_PASS)
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO task_journal (agent_id, task_type, task_description, result, success, chat_id, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            ("specter_voss", f"telegram_{role}", content[:500], content[:500], success, str(chat_id), datetime.now())
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.warning(f"Journal log failed: {e}")


def update_heartbeat():
    """Send a heartbeat to baza's Redis so the dashboard sees Specter as online."""
    try:
        import redis as r
        host = os.environ.get("BAZA_REDIS_HOST", "100.127.118.103")
        port = int(os.environ.get("BAZA_REDIS_PORT", "6379"))
        client = r.Redis(host=host, port=port, decode_responses=True)
        payload = json.dumps({
            "agent_id": "specter_voss",
            "model": "openclaw+ollama-cloud",
            "status": "idle",
            "ts": int(time.time())
        })
        client.set("baza:heartbeat:specter_voss", payload, ex=120)
        client.close()
    except Exception as e:
        logger.warning(f"Heartbeat failed: {e}")


# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    logger.info("Specter Voss Telegram Bridge starting...")
    logger.info(f"Bot token: ...{BOT_TOKEN[-8:]}")
    logger.info(f"OpenClaw: {OPENCLAW_BIN}")
    logger.info(f"DB: {DB_USER}@{DB_HOST}/{DB_NAME}")

    # Verify bot
    me = tg_request("getMe")
    if not me.get("ok"):
        logger.error(f"Bot check failed: {me}")
        return
    bot_name = me["result"]["username"]
    logger.info(f"Connected as @{bot_name}")

    # Get current offset to skip old messages
    initial = get_updates(offset=0, timeout=1)
    offset = 0
    if initial.get("ok") and initial.get("result"):
        offset = initial["result"][-1]["update_id"] + 1
        logger.info(f"Skipping {len(initial['result'])} backlogged messages, starting at offset {offset}")

    last_heartbeat = 0

    while True:
        try:
            # Heartbeat every 60s
            if time.time() - last_heartbeat > 60:
                update_heartbeat()
                last_heartbeat = time.time()

            updates = get_updates(offset=offset, timeout=25)
            if not updates.get("ok"):
                time.sleep(5)
                continue

            for update in updates.get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                if not msg or "text" not in msg:
                    continue

                chat_id = msg["chat"]["id"]
                text = msg["text"]
                user = msg.get("from", {})
                user_name = user.get("first_name", "User")

                logger.info(f"[{chat_id}] {user_name}: {text[:80]}")

                # Show typing
                send_chat_action(chat_id, "typing")

                # Log user message
                journal_log(chat_id, "user", f"{user_name}: {text}")

                # Handle special commands
                if text.startswith("/start"):
                    send_message(chat_id,
                        "Specter Voss online.\n\n"
                        "Sharp. Quiet. Lethal efficiency.\n"
                        "I see all of Baza. I find problems before they find us.\n\n"
                        "What do you need?")
                    continue
                if text.startswith("/help"):
                    send_message(chat_id,
                        "Commands:\n"
                        "/scan - Run baza_scan (infrastructure check)\n"
                        "/pulse - All agent status\n"
                        "/logs - Service log scan\n"
                        "/skills - List available skills\n"
                        "Or just ask me anything in plain English.")
                    continue

                # Route to OpenClaw with chat_id as session
                try:
                    reply = call_openclaw(str(chat_id), text)
                except Exception as e:
                    logger.error(f"call_openclaw error: {e}")
                    reply = f"Error reaching brain: {e}"

                # Send reply
                send_message(chat_id, reply)
                journal_log(chat_id, "assistant", reply)
                logger.info(f"[{chat_id}] Replied: {reply[:80]}")

        except KeyboardInterrupt:
            logger.info("Shutting down")
            break
        except Exception as e:
            logger.error(f"Main loop error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
