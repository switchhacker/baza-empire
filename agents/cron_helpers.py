#!/usr/bin/env python3
"""
Shared helpers for agent cron scripts.
Every cron script imports this for common functions:
  - Ollama inference
  - Telegram notification
  - Artifact saving
  - Event publishing
  - DB access
"""
import os, sys, json, subprocess, datetime, urllib.request, sqlite3, logging

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, FRAMEWORK_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(FRAMEWORK_DIR, "configs", "secrets.env"))

TELEGRAM_TOKEN = os.getenv("TELEGRAM_SIMON_BATELY", "")
SERGE_CHAT_ID = os.getenv("SERGE_CHAT_ID", "8551331144")
OLLAMA_URL = os.getenv("OLLAMA_HOST", "http://localhost:11434")
DB_PATH = os.path.join(FRAMEWORK_DIR, "dashboard", "baza_projects.db")
ARTIFACTS_DIR = os.path.join(FRAMEWORK_DIR, "dashboard", "artifacts")

log = logging.getLogger("cron")


def run_cmd(cmd: str, timeout: int = 10) -> str:
    try:
        return subprocess.check_output(cmd, shell=True, stderr=subprocess.DEVNULL, timeout=timeout).decode().strip()
    except Exception:
        return ""


def ollama_generate(model: str, system_prompt: str, user_prompt: str, max_tokens: int = 800) -> str:
    """Run a non-streaming Ollama inference."""
    # Estimate context needed: ~1.3 tokens per word, plus output budget
    total_chars = len(system_prompt) + len(user_prompt)
    needed_ctx = max(8192, int(total_chars / 3) + max_tokens + 512)
    payload = json.dumps({
        "model": model, "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "options": {
            "num_predict": max_tokens,
            "num_ctx": needed_ctx,
            "temperature": 0.7
        }
    }).encode()
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/chat", data=payload,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as r:
            data = json.loads(r.read())
            content = data.get("message", {}).get("content", "")
            if not content:
                log.warning(f"Ollama returned empty for model={model}, ctx={needed_ctx}, input_chars={total_chars}")
            return content
    except Exception as e:
        log.error(f"Ollama error: {e}")
        return f"(LLM unavailable: {e})"


def send_telegram(message: str, token: str = None, chat_id: str = None):
    """Send a Telegram message to Serge (markdown → rich HTML, auto-chunked)."""
    tok = token or TELEGRAM_TOKEN
    cid = chat_id or SERGE_CHAT_ID
    if not tok or not cid:
        log.warning("No Telegram token/chat_id configured")
        return
    try:
        from core.telegram_fmt import post_html
        post_html(tok, cid, message)
    except Exception as e:
        log.error(f"Telegram send failed: {e}")


def save_artifact(project_id: str, filename: str, content: str) -> str:
    """Save report as artifact file."""
    art_dir = os.path.join(ARTIFACTS_DIR, project_id)
    os.makedirs(art_dir, exist_ok=True)
    path = os.path.join(art_dir, filename)
    with open(path, "w") as f:
        f.write(content)
    return path


def publish_event(agent_id: str, event_type: str, data: dict):
    """Publish event to Redis event bus."""
    try:
        from core.event_bus import publish_sync
        publish_sync(agent_id, event_type, data)
    except Exception as e:
        log.warning(f"Event publish failed: {e}")


def get_db():
    """Get SQLite connection to baza_projects.db."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_pg():
    """Get PostgreSQL connection to baza_agents."""
    import psycopg2
    return psycopg2.connect(host="localhost", port=5432, dbname="baza_agents",
                            user="switchhacker",
                            password=os.environ.get("DB_PASSWORD", "baza2026"))


def log_activity(agent_id: str, summary: str, task_type: str = "cron_task",
                 requested_by: str = "cron", status: str = "completed", **kwargs):
    """Log an entry to the activity feed (task_journal in PostgreSQL)."""
    try:
        from core.context_db import journal_log
        journal_log(agent_id=agent_id, task_type=task_type,
                    task_description=summary, action_summary=summary,
                    requested_by=requested_by, status=status, **kwargs)
    except Exception as e:
        log.warning(f"log_activity failed: {e}")


def today():
    return datetime.date.today().isoformat()


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
