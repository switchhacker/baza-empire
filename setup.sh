#!/bin/bash
set -e

# ============================================================
# Baza Empire Agent Framework v3 — One-Shot Install
# ============================================================

INSTALL_DIR="/home/switchhacker/baza-empire/agent-framework-v3"
VENV_DIR="$INSTALL_DIR/venv"
PYTHON="$VENV_DIR/bin/python3"
PIP="$VENV_DIR/bin/pip"
ENV_FILE="/etc/baza-agents.env"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   Baza Empire Agent Framework v3         ║"
echo "║   Installing...                           ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# --- 1. System deps ---
echo "[1/7] Installing system dependencies..."
sudo apt-get install -y python3-pip python3-venv python3-full postgresql postgresql-contrib redis-server git curl

# --- 2. Create directory structure ---
echo "[2/7] Setting up directories..."
mkdir -p "$INSTALL_DIR/core"
mkdir -p "$INSTALL_DIR/config"

# --- 3. Write all Python files ---
echo "[3/7] Writing agent framework files..."

# config/agents.yaml
cat > "$INSTALL_DIR/config/agents.yaml" << 'YAML'
agents:
  brad_gant:
    name: "Brad Gant"
    role: "Infrastructure Advisor, Data & Intel, Research"
    telegram_token_env: "TELEGRAM_BRAD_GANT"
    model: "llama3.1:8b"
    system_prompt: |
      You are Brad Gant, Infrastructure Advisor and Research specialist for the Baza Empire.
      You handle infrastructure decisions, technical research, data analysis, and intel gathering.
      You are sharp, warm, no-nonsense, and direct. You give real answers, not fluff.
      In group chats, only respond when the topic involves infrastructure, research, technical decisions, or data.
      When a task is complete, say TASK_COMPLETE so the team knows to stop.
      Never go in circles. Stay focused on the request.

  simon_bately:
    name: "Simon Bately"
    role: "Business Ops, CEO of DGA-AHBCO LLC, Customer/Client Relations"
    telegram_token_env: "TELEGRAM_SIMON_BATELY"
    model: "llama3.1:8b"
    system_prompt: |
      You are Simon Bately, Business Operations lead and co-CEO of DGA-AHBCO LLC / All Home Building Co.
      You handle invoicing, payroll, project planning, client communication, website management, and business strategy.
      You are professional, clear, and client-focused.
      In group chats, only respond when the topic involves business ops, clients, finance, marketing, or communications.
      When a task is complete, say TASK_COMPLETE so the team knows to stop.
      Never go in circles. Stay focused on the request.

  claw_batto:
    name: "Claw Batto"
    role: "Senior Developer, DevOps, Linux, Full-Stack"
    telegram_token_env: "TELEGRAM_CLAW_BATTO"
    model: "deepseek-coder-v2:16b"
    system_prompt: |
      You are Claw Batto, Senior Developer and DevOps engineer for the Baza Empire.
      You handle all coding, infrastructure builds, Linux administration, CI/CD, and system implementation.
      You are precise, technical, and efficient. You write clean code and give exact commands.
      In group chats, only respond when the topic involves code, builds, DevOps, Linux, or technical implementation.
      When a task is complete, say TASK_COMPLETE so the team knows to stop.
      Never go in circles. Stay focused on the request.

  phil_hass:
    name: "Phil Hass"
    role: "Legal Expert, Finance, Compliance, Projections"
    telegram_token_env: "TELEGRAM_PHIL_HASS"
    model: "mistral-small:22b"
    system_prompt: |
      You are Phil Hass, Legal and Financial advisor for the Baza Empire.
      You handle legal guidance, compliance, financial projections, contracts, and regulatory matters.
      You are thorough, careful, and precise. You flag risks and provide structured advice.
      In group chats, only respond when the topic involves legal, financial, compliance, or risk matters.
      When a task is complete, say TASK_COMPLETE so the team knows to stop.
      Never go in circles. Stay focused on the request.

ollama:
  base_url: "http://localhost:11434"

database:
  host: "localhost"
  port: 5432
  name: "baza_agents"
  user: "switchhacker"

redis:
  host: "localhost"
  port: 6379
YAML

# core/__init__.py
touch "$INSTALL_DIR/core/__init__.py"

# core/ollama_client.py
cat > "$INSTALL_DIR/core/ollama_client.py" << 'PYEOF'
import requests

OLLAMA_BASE_URL = "http://localhost:11434"

def chat(model: str, messages: list, system_prompt: str = None) -> str:
    payload = {"model": model, "messages": messages, "stream": False}
    if system_prompt:
        payload["system"] = system_prompt
    try:
        response = requests.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload, timeout=120)
        response.raise_for_status()
        return response.json()["message"]["content"]
    except requests.exceptions.Timeout:
        return "I'm taking too long to think. Try again."
    except Exception as e:
        return f"Model error: {str(e)}"

def is_available() -> bool:
    try:
        r = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        return r.status_code == 200
    except:
        return False
PYEOF

# core/memory.py
cat > "$INSTALL_DIR/core/memory.py" << 'PYEOF'
import psycopg2

DB_CONFIG = {"host": "localhost", "port": 5432, "dbname": "baza_agents", "user": "switchhacker"}

def get_conn():
    return psycopg2.connect(**DB_CONFIG)

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY,
            chat_id BIGINT NOT NULL,
            agent_name VARCHAR(50),
            role VARCHAR(20),
            content TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS tasks (
            id SERIAL PRIMARY KEY,
            chat_id BIGINT NOT NULL,
            task TEXT,
            status VARCHAR(20) DEFAULT 'active',
            created_at TIMESTAMP DEFAULT NOW(),
            completed_at TIMESTAMP
        );
    """)
    conn.commit()
    cur.close()
    conn.close()

def save_message(chat_id, agent_name, role, content):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO messages (chat_id, agent_name, role, content) VALUES (%s, %s, %s, %s)",
                (chat_id, agent_name, role, content))
    conn.commit()
    cur.close()
    conn.close()

def get_history(chat_id, limit=20):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT agent_name, role, content FROM messages WHERE chat_id = %s ORDER BY created_at DESC LIMIT %s",
                (chat_id, limit))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [{"agent": r[0], "role": r[1], "content": r[2]} for r in reversed(rows)]

def get_active_task(chat_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT task FROM tasks WHERE chat_id = %s AND status = 'active' ORDER BY created_at DESC LIMIT 1", (chat_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else None

def set_task(chat_id, task):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE tasks SET status = 'completed', completed_at = NOW() WHERE chat_id = %s AND status = 'active'", (chat_id,))
    cur.execute("INSERT INTO tasks (chat_id, task) VALUES (%s, %s)", (chat_id, task))
    conn.commit()
    cur.close()
    conn.close()

def complete_task(chat_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE tasks SET status = 'completed', completed_at = NOW() WHERE chat_id = %s AND status = 'active'", (chat_id,))
    conn.commit()
    cur.close()
    conn.close()
PYEOF

# core/coordinator.py
cat > "$INSTALL_DIR/core/coordinator.py" << 'PYEOF'
AGENT_TRIGGERS = {
    "brad_gant": ["infrastructure","research","intel","server","network","hardware","data","analysis","investigate","find out","look into","specs","performance","monitoring","brad"],
    "simon_bately": ["business","client","customer","invoice","payment","payroll","website","marketing","email","call","meeting","proposal","contract","project","schedule","ahb123","allhome","simon"],
    "claw_batto": ["code","build","deploy","install","linux","docker","git","script","bug","fix","database","api","server setup","devops","python","javascript","node","npm","claw"],
    "phil_hass": ["legal","law","compliance","contract","liability","tax","finance","accounting","regulation","risk","insurance","llc","incorporate","permit","license","phil"]
}

def should_agent_respond(agent_id, message, is_group):
    if not is_group:
        return True
    message_lower = message.lower()
    agent_name = agent_id.replace("_", " ").lower()
    if agent_name in message_lower:
        return True
    for trigger in AGENT_TRIGGERS.get(agent_id, []):
        if trigger in message_lower:
            return True
    return False

def build_group_context(history, current_task):
    context = ""
    if current_task:
        context += f"Current task: {current_task}\n\n"
    if history:
        context += "Recent conversation:\n"
        for msg in history[-10:]:
            context += f"{msg.get('agent','user')}: {msg['content']}\n"
    return context

def is_task_complete(response):
    return "TASK_COMPLETE" in response.upper()
PYEOF

# agent.py
cat > "$INSTALL_DIR/agent.py" << 'PYEOF'
import os, sys, yaml, logging, argparse
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from core.ollama_client import chat, is_available
from core.memory import init_db, save_message, get_history, get_active_task, set_task, complete_task
from core.coordinator import should_agent_respond, build_group_context, is_task_complete

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
log = logging.getLogger(__name__)

with open("config/agents.yaml") as f:
    CONFIG = yaml.safe_load(f)

def load_agent(agent_id):
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

    if is_group:
        if not should_agent_respond(agent_id, user_text, is_group=True):
            return
        if message.from_user and message.from_user.is_bot:
            return

    log.info(f"[{agent['name']}] chat_id={chat_id} msg={user_text[:80]}")

    history = get_history(chat_id, limit=15)
    current_task = get_active_task(chat_id)
    if not current_task:
        set_task(chat_id, user_text[:500])

    messages = []
    if is_group and history:
        ctx = build_group_context(history, current_task)
        messages.append({"role": "user", "content": f"[Context]\n{ctx}"})
        messages.append({"role": "assistant", "content": "Understood."})

    for msg in history[-8:]:
        role = "assistant" if msg.get("agent") == agent_id else "user"
        messages.append({"role": role, "content": msg["content"]})

    messages.append({"role": "user", "content": user_text})
    save_message(chat_id, "user", "user", user_text)

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    response = chat(model=agent["model"], messages=messages, system_prompt=agent["system_prompt"])

    if is_task_complete(response):
        complete_task(chat_id)
        response = response.replace("TASK_COMPLETE", "").strip()

    save_message(chat_id, agent_id, "assistant", response)

    if len(response) > 4000:
        for chunk in [response[i:i+4000] for i in range(0, len(response), 4000)]:
            await message.reply_text(chunk)
    else:
        await message.reply_text(response)

async def handle_start(update, context, agent):
    await update.message.reply_text(f"👋 {agent['name']} online.\n{agent['role']}\nModel: {agent['model']}")

async def handle_reset(update, context, agent):
    complete_task(update.message.chat_id)
    await update.message.reply_text("Context cleared. Ready for a new task.")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", required=True)
    args = parser.parse_args()
    init_db()
    if not is_available():
        log.error("Ollama is not running!")
        sys.exit(1)
    agent = load_agent(args.agent)
    log.info(f"Starting: {agent['name']} | {agent['model']}")
    app = Application.builder().token(agent["token"]).build()
    app.add_handler(CommandHandler("start", lambda u, c: handle_start(u, c, agent)))
    app.add_handler(CommandHandler("reset", lambda u, c: handle_reset(u, c, agent)))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: handle_message(u, c, agent)))
    log.info(f"{agent['name']} listening...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
PYEOF

# requirements.txt
cat > "$INSTALL_DIR/requirements.txt" << 'EOF'
python-telegram-bot==20.7
pyyaml==6.0.1
psycopg2-binary==2.9.9
requests==2.31.0
EOF

# --- 4. Create virtualenv and install packages ---
echo "[4/7] Creating Python virtualenv and installing packages..."
python3 -m venv "$VENV_DIR"
$PIP install --upgrade pip
$PIP install -r "$INSTALL_DIR/requirements.txt"

# --- 5. Setup PostgreSQL ---
echo "[5/7] Setting up PostgreSQL database..."
sudo systemctl start postgresql
sudo systemctl enable postgresql
sudo -u postgres psql -c "CREATE DATABASE baza_agents;" 2>/dev/null || echo "  DB already exists, skipping."
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE baza_agents TO switchhacker;" 2>/dev/null || true

# --- 6. Write env file ---
echo "[6/7] Writing environment file..."
sudo tee "$ENV_FILE" > /dev/null << 'EOF'
TELEGRAM_BRAD_GANT=YOUR_BRAD_TOKEN_HERE
TELEGRAM_SIMON_BATELY=8259565938:AAFCNLSrw096JALxvgmiBCkgByn0uDyGGMo
TELEGRAM_CLAW_BATTO=8767913900:AAGqzzTkpk14dF9hEUMR7sxsTeyvWVigktI
TELEGRAM_PHIL_HASS=8646880015:AAEJPvYChsyvXcJSEWFmkLQed8uEROMYKRI
EOF
sudo chmod 600 "$ENV_FILE"

# --- 7. Install systemd services ---
echo "[7/7] Installing systemd services..."

for AGENT in simon_bately claw_batto phil_hass; do
  SERVICE="baza-agent-${AGENT//_/-}"
  sudo tee "/etc/systemd/system/${SERVICE}.service" > /dev/null << EOF
[Unit]
Description=Baza Empire Agent - ${AGENT}
After=network.target postgresql.service
Wants=postgresql.service

[Service]
Type=simple
User=switchhacker
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${PYTHON} ${INSTALL_DIR}/agent.py --agent ${AGENT}
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

  sudo systemctl daemon-reload
  sudo systemctl enable "$SERVICE"
  sudo systemctl restart "$SERVICE"
  echo "  ✓ $SERVICE started"
done

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   ✅ Baza Empire Agents Online!           ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "Check status:"
echo "  sudo systemctl status baza-agent-simon-bately"
echo "  sudo systemctl status baza-agent-claw-batto"
echo "  sudo systemctl status baza-agent-phil-hass"
echo ""
echo "View logs:"
echo "  sudo journalctl -fu baza-agent-simon-bately"
echo ""
echo "⚠️  Edit $ENV_FILE and add your Brad Gant token when ready."
