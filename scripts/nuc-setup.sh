#!/bin/bash
set -euo pipefail
# ═══════════════════════════════════════════════════════════════════════════════
# Baza Empire — NUC "Specter Voss" Setup Script
# Run this on a fresh Ubuntu 24.04 install on the NUC
# ═══════════════════════════════════════════════════════════════════════════════

echo "═══════════════════════════════════════════"
echo "  BAZA EMPIRE — NUC Specter Voss Setup"
echo "═══════════════════════════════════════════"

# ── 1. System Updates ─────────────────────────────────────────────────────────
echo ""
echo "[1/10] System updates..."
sudo apt update && sudo apt upgrade -y
sudo apt install -y \
    git curl wget build-essential python3 python3-pip python3-venv \
    nodejs npm docker.io docker-compose postgresql-client redis-tools \
    chromium-browser fonts-liberation libappindicator3-1 \
    libnss3 libatk-bridge2.0-0 libx11-xcb1

# Add user to docker group
sudo usermod -aG docker "$USER"

# ── 2. Tailscale VPN ─────────────────────────────────────────────────────────
echo ""
echo "[2/10] Installing Tailscale..."
if ! command -v tailscale &>/dev/null; then
    curl -fsSL https://tailscale.com/install.sh | sh
    echo ""
    echo ">>> Run: sudo tailscale up"
    echo ">>> Then authenticate in browser"
    echo ">>> Note your Tailscale IP (100.x.y.z)"
    echo ">>> Press ENTER when done..."
    read -r
else
    echo "  Tailscale already installed"
    tailscale ip -4 2>/dev/null || echo "  (not connected yet)"
fi

# ── 3. Ollama ─────────────────────────────────────────────────────────────────
echo ""
echo "[3/10] Installing Ollama..."
if ! command -v ollama &>/dev/null; then
    curl -fsSL https://ollama.com/install.sh | sh
else
    echo "  Ollama already installed"
fi

# Pull cloud model manifest (doesn't download weights — they run on Ollama cloud)
echo "  Testing Ollama cloud model access..."
echo "  (You need OLLAMA_API_KEY set — get it from ollama.com/settings)"

# ── 4. OpenClaw ───────────────────────────────────────────────────────────────
echo ""
echo "[4/10] Installing OpenClaw..."
if ! command -v openclaw &>/dev/null; then
    npm install -g openclaw@latest
    echo "  OpenClaw installed"
else
    echo "  OpenClaw already installed"
    openclaw --version 2>/dev/null || true
fi

# ── 5. n8n (workflow automation) ──────────────────────────────────────────────
echo ""
echo "[5/10] Setting up n8n via Docker..."
mkdir -p ~/.n8n
if ! docker ps -a --format '{{.Names}}' | grep -q '^n8n$'; then
    docker run -d \
        --name n8n \
        --restart unless-stopped \
        -p 5678:5678 \
        -v ~/.n8n:/home/node/.n8n \
        -e N8N_SECURE_COOKIE=false \
        docker.n8n.io/n8nio/n8n
    echo "  n8n running on http://localhost:5678"
else
    echo "  n8n container already exists"
fi

# ── 6. SearXNG (private search) ──────────────────────────────────────────────
echo ""
echo "[6/10] Setting up SearXNG..."
if ! docker ps -a --format '{{.Names}}' | grep -q '^searxng$'; then
    docker run -d \
        --name searxng \
        --restart unless-stopped \
        -p 8080:8080 \
        -v ~/searxng:/etc/searxng \
        -e SEARXNG_BASE_URL=http://localhost:8080/ \
        searxng/searxng
    echo "  SearXNG running on http://localhost:8080"
else
    echo "  SearXNG container already exists"
fi

# ── 7. Perplexica (AI search) ────────────────────────────────────────────────
echo ""
echo "[7/10] Setting up Perplexica..."
if [ ! -d ~/perplexica ]; then
    git clone https://github.com/ItzCrazyKns/Perplexica.git ~/perplexica
    cd ~/perplexica
    cp sample.config.toml config.toml
    # Configure to use local SearXNG + Ollama
    sed -i 's|SEARXNG_URL = .*|SEARXNG_URL = "http://localhost:8080"|' config.toml 2>/dev/null || true
    sed -i 's|OLLAMA_URL = .*|OLLAMA_URL = "http://localhost:11434"|' config.toml 2>/dev/null || true
    docker compose up -d 2>/dev/null || echo "  (run 'cd ~/perplexica && docker compose up -d' manually)"
    cd -
    echo "  Perplexica set up in ~/perplexica"
else
    echo "  Perplexica already cloned"
fi

# ── 8. Browser-Use + Crawl4AI ────────────────────────────────────────────────
echo ""
echo "[8/10] Setting up Browser-Use and Crawl4AI..."
python3 -m venv ~/baza-tools-venv
source ~/baza-tools-venv/bin/activate
pip install --upgrade pip
pip install browser-use crawl4ai playwright
playwright install chromium
deactivate
echo "  Browser-Use + Crawl4AI installed in ~/baza-tools-venv"

# ── 9. Claude Code CLI ───────────────────────────────────────────────────────
echo ""
echo "[9/13] Installing Claude Code CLI..."
if ! command -v claude &>/dev/null; then
    npm install -g @anthropic-ai/claude-code
    echo "  Claude Code CLI installed"
    echo "  >>> Authenticate with: claude login"
    echo "  >>> Or set ANTHROPIC_API_KEY in your environment"
else
    echo "  Claude Code CLI already installed"
    claude --version 2>/dev/null || true
fi

# ── 10. Gemini CLI ───────────────────────────────────────────────────────────
echo ""
echo "[10/13] Installing Gemini CLI..."
if ! command -v gemini &>/dev/null; then
    npm install -g @google/gemini-cli
    echo "  Gemini CLI installed"
    echo "  >>> Authenticate with: gemini login"
    echo "  >>> Or set GOOGLE_API_KEY in your environment"
    echo "  >>> Free tier: 60 req/min with Gemini 2.5 Pro"
else
    echo "  Gemini CLI already installed"
    gemini --version 2>/dev/null || true
fi

# ── 11. Claw CLI 2.0 (Baza Empire dev agent) ────────────────────────────────
echo ""
echo "[11/13] Installing Claw CLI 2.0..."
BAZA_DIR_CHECK=~/baza-empire/agent-framework-v3
if [ -f "$BAZA_DIR_CHECK/agents/claw_batto/claw_cli.py" ]; then
    # Install the launcher to /usr/local/bin
    sudo tee /usr/local/bin/claw > /dev/null << 'CLAWEOF'
#!/bin/bash
# Claw Batto — Dev CLI Agent launcher
export PYTHONPATH="/home/switchhacker/baza-empire/agent-framework-v3"
export OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"
export CLAW_MODEL="${CLAW_MODEL:-mistral-small:22b}"
cd "${CLAW_CWD:-/home/switchhacker/baza-empire/agent-framework-v3}"
exec "/home/switchhacker/baza-empire/agent-framework-v3/venv/bin/python" "/home/switchhacker/baza-empire/agent-framework-v3/agents/claw_batto/claw_cli.py" "$@"
CLAWEOF
    sudo chmod +x /usr/local/bin/claw
    echo "  Claw CLI 2.0 installed at /usr/local/bin/claw"
    echo "  Usage: claw, claw \"fix the bug\", claw --doctor"
else
    echo "  Baza framework not yet cloned — Claw CLI will be installed after Step 12"
    echo "  Run: bash agents/claw_batto/install_claw_cli.sh"
fi

# ── 12. Clone Baza Framework ─────────────────────────────────────────────────
echo ""
echo "[12/13] Setting up Baza Agent Framework..."
BAZA_DIR=~/baza-empire/agent-framework-v3
if [ ! -d "$BAZA_DIR" ]; then
    echo "  >>> Clone the repo or copy from main server:"
    echo "  >>> scp -r switchhacker@baza-main:~/baza-empire ~/baza-empire"
    echo "  >>> Or: git clone <your-gitea-url> ~/baza-empire/agent-framework-v3"
else
    echo "  Baza framework found at $BAZA_DIR"
fi

if [ -d "$BAZA_DIR" ]; then
    cd "$BAZA_DIR"
    python3 -m venv venv
    source venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt 2>/dev/null || echo "  (install deps manually if needed)"
    deactivate
fi

# ── 13. Environment File ─────────────────────────────────────────────────────
echo ""
echo "[13/13] Creating environment config..."
ENV_FILE=~/baza-empire/.env.nuc
if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" << 'ENVEOF'
# ═══════════════════════════════════════════════════════════════════════════════
# Baza Empire — NUC Specter Voss Environment
# ═══════════════════════════════════════════════════════════════════════════════

# ── Main Server Connection (Tailscale IPs) ────────────────────────────────────
# Replace 100.x.y.z with your main server's Tailscale IP
BAZA_DB_HOST=100.127.118.103
BAZA_DB_PORT=5432
BAZA_DB_NAME=baza_agents
BAZA_DB_USER=switchhacker
DB_PASSWORD=baza2026

BAZA_REDIS_URL=redis://100.127.118.103:6379/1
BAZA_REDIS_HOST=100.127.118.103
BAZA_REDIS_PORT=6379

# ── Ollama Cloud ──────────────────────────────────────────────────────────────
# Get your API key from https://ollama.com/settings
OLLAMA_API_KEY=your_ollama_api_key_here
OLLAMA_URL=http://127.0.0.1:11434

# ── LiteLLM (fallback to main server's cloud proxy) ──────────────────────────
LITELLM_URL=http://100.127.118.103:4000
LITELLM_MASTER_KEY=baza-litellm-internal

# ── Telegram Bot ──────────────────────────────────────────────────────────────
# Create a new bot via @BotFather on Telegram
TELEGRAM_SPECTER_VOSS=your_telegram_bot_token_here
# Serge's Telegram chat ID (for stealth upgrade approvals)
# Get this by messaging the bot and checking getUpdates
SERGE_CHAT_ID=your_chat_id_here

# Auto-approve categories (comma-separated): deploy,skill,config,restart,install,migration
# Leave empty to require manual approval for everything
AUTO_APPROVE_CATEGORIES=

# Main server SSH (for stealth upgrades)
BAZA_MAIN_HOST=baza-main
BAZA_MAIN_USER=switchhacker

# ── AI CLI Tools ──────────────────────────────────────────────────────────────
# Claude Code: get key from console.anthropic.com
ANTHROPIC_API_KEY=your_anthropic_api_key_here

# Gemini CLI: get key from aistudio.google.com (free 60 req/min)
GOOGLE_API_KEY=your_google_api_key_here

# Claw CLI: uses local Ollama (no API key needed)
CLAW_MODEL=mistral-small:22b

# ── NUC Services ──────────────────────────────────────────────────────────────
SEARXNG_URL=http://localhost:8080
PERPLEXICA_URL=http://localhost:3000
N8N_URL=http://localhost:5678

# ── Location (for weather/news skills) ────────────────────────────────────────
EMPIRE_LOCATION=Philadelphia
ENVEOF
    echo "  Created $ENV_FILE — EDIT THIS with real values!"
else
    echo "  $ENV_FILE already exists"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  NUC Specter Voss Setup Complete!"
echo "═══════════════════════════════════════════════════════════"
echo ""
echo "  NEXT STEPS:"
echo "  1. Edit ~/baza-empire/.env.nuc with real values"
echo "     - Set BAZA_DB_HOST to main server Tailscale IP"
echo "     - Set OLLAMA_API_KEY from ollama.com/settings"
echo "     - Set TELEGRAM_SPECTER_VOSS from @BotFather"
echo ""
echo "  2. On MAIN SERVER — allow NUC connections:"
echo "     sudo nano /etc/postgresql/16/main/postgresql.conf"
echo "       -> listen_addresses = '*'"
echo "     sudo nano /etc/postgresql/16/main/pg_hba.conf"
echo "       -> host baza_agents switchhacker 100.0.0.0/8 md5"
echo "     sudo systemctl restart postgresql"
echo "     sudo nano /etc/redis/redis.conf"
echo "       -> bind 0.0.0.0"
echo "       -> requirepass your_redis_password"
echo "     sudo systemctl restart redis"
echo ""
echo "  3. Test connections:"
echo "     psql -h \$BAZA_DB_HOST -U switchhacker -d baza_agents"
echo "     redis-cli -h \$BAZA_REDIS_HOST ping"
echo "     ollama run gemma4:31b-cloud 'hello'"
echo ""
echo "  4. Launch Specter Voss:"
echo "     cd ~/baza-empire/agent-framework-v3"
echo "     source .env.nuc  # or use: set -a; source ../.env.nuc; set +a"
echo "     source venv/bin/activate"
echo "     python main.py specter"
echo ""
echo "  SERVICES:"
echo "  - n8n:        http://localhost:5678"
echo "  - SearXNG:    http://localhost:8080"
echo "  - Perplexica: http://localhost:3000 (after docker compose up)"
echo "  - Ollama:     http://localhost:11434"
echo ""
echo "  CLI TOOLS:"
echo "  - claude      Claude Code CLI (Anthropic)"
echo "  - gemini      Gemini CLI (Google, free 60 req/min)"
echo "  - claw        Claw CLI 2.0 (Baza Empire, local Ollama)"
echo "═══════════════════════════════════════════════════════════"
