#!/bin/bash
set -euo pipefail
# ═══════════════════════════════════════════════════════════════════════════════
# Baza Empire — OpenClaw + Specter Voss Integration Setup
# Run AFTER nuc-setup.sh has completed
# ═══════════════════════════════════════════════════════════════════════════════

echo "═══════════════════════════════════════════"
echo "  OpenClaw + Specter Voss Integration"
echo "═══════════════════════════════════════════"

BAZA_DIR="${HOME}/baza-empire/agent-framework-v3"
OPENCLAW_DIR="${BAZA_DIR}/agents/specter_voss/openclaw"
SPECTER_HOME="${HOME}/.specter"

# ── 1. Verify OpenClaw is installed ──────────────────────────────────────────
echo ""
echo "[1/7] Checking OpenClaw..."
if ! command -v openclaw &>/dev/null; then
    echo "  Installing OpenClaw..."
    npm install -g openclaw@latest
fi
echo "  OpenClaw: $(openclaw --version 2>/dev/null || echo 'installed')"

# ── 2. Verify Ollama + cloud models ─────────────────────────────────────────
echo ""
echo "[2/7] Checking Ollama cloud models..."
if [ -z "${OLLAMA_API_KEY:-}" ]; then
    echo "  WARNING: OLLAMA_API_KEY not set!"
    echo "  Get your key from https://ollama.com/settings"
    echo "  Set it in ~/.specter/.env or ~/baza-empire/.env.nuc"
fi

# Test cloud model access
echo "  Testing cloud model access..."
ollama list 2>/dev/null | head -5 || echo "  (ollama not running or no models pulled)"

# ── 3. Create Specter home directory ─────────────────────────────────────────
echo ""
echo "[3/7] Setting up Specter home..."
mkdir -p "${SPECTER_HOME}"/{logs,upgrade_logs,sessions,cache}

# ── 4. Link OpenClaw config ─────────────────────────────────────────────────
echo ""
echo "[4/7] Configuring OpenClaw for Specter..."

# Create OpenClaw config directory
OPENCLAW_CONFIG="${HOME}/.config/openclaw"
mkdir -p "${OPENCLAW_CONFIG}"

# Symlink Specter's SOUL/IDENTITY/USER to OpenClaw's config
ln -sf "${OPENCLAW_DIR}/SOUL.md" "${OPENCLAW_CONFIG}/SOUL.md"
ln -sf "${OPENCLAW_DIR}/IDENTITY.md" "${OPENCLAW_CONFIG}/IDENTITY.md"
ln -sf "${OPENCLAW_DIR}/USER.md" "${OPENCLAW_CONFIG}/USER.md"

echo "  Linked SOUL.md, IDENTITY.md, USER.md"

# Create OpenClaw settings.json
cat > "${OPENCLAW_CONFIG}/settings.json" << SETTINGSEOF
{
  "name": "Specter Voss",
  "model": {
    "provider": "ollama",
    "model": "glm-5:cloud",
    "baseUrl": "http://localhost:11434"
  },
  "telegram": {
    "enabled": true
  },
  "tools": {
    "baza_bridge": {
      "command": "${BAZA_DIR}/venv/bin/python",
      "args": ["${OPENCLAW_DIR}/tools/baza_bridge.py"],
      "description": "Run any Baza Empire skill"
    }
  },
  "mcp": {
    "servers": [
      {
        "name": "baza-filesystem",
        "command": "npx",
        "args": ["-y", "@anthropic-ai/mcp-filesystem", "${BAZA_DIR}"]
      }
    ]
  },
  "memory": {
    "enabled": true,
    "dir": "${SPECTER_HOME}/sessions"
  }
}
SETTINGSEOF
echo "  Created OpenClaw settings.json"

# ── 5. Create the 'specter' launcher command ─────────────────────────────────
echo ""
echo "[5/7] Installing 'specter' command..."

sudo tee /usr/local/bin/specter > /dev/null << 'LAUNCHEREOF'
#!/bin/bash
# Specter Voss — OpenClaw launcher for Baza Empire
set -a
[ -f "${HOME}/baza-empire/.env.nuc" ] && source "${HOME}/baza-empire/.env.nuc"
set +a

export BAZA_FRAMEWORK_DIR="${HOME}/baza-empire/agent-framework-v3"
export PYTHONPATH="${BAZA_FRAMEWORK_DIR}"

case "${1:-}" in
    run|skill)
        # Direct skill execution: specter run baza_scan
        shift
        exec "${BAZA_FRAMEWORK_DIR}/venv/bin/python" \
            "${BAZA_FRAMEWORK_DIR}/agents/specter_voss/openclaw/tools/baza_bridge.py" "$@"
        ;;
    scan)
        # Quick scan shortcut: specter scan
        exec "${BAZA_FRAMEWORK_DIR}/venv/bin/python" \
            "${BAZA_FRAMEWORK_DIR}/agents/specter_voss/openclaw/tools/baza_bridge.py" baza_scan
        ;;
    pulse)
        # Agent pulse shortcut: specter pulse [agent_id]
        shift
        ARGS="{}"
        [ -n "${1:-}" ] && ARGS="{\"agent\":\"$1\"}"
        exec "${BAZA_FRAMEWORK_DIR}/venv/bin/python" \
            "${BAZA_FRAMEWORK_DIR}/agents/specter_voss/openclaw/tools/baza_bridge.py" agent_pulse "$ARGS"
        ;;
    logs)
        # Log scan shortcut: specter logs [service]
        shift
        ARGS="{}"
        [ -n "${1:-}" ] && ARGS="{\"service\":\"$1\"}"
        exec "${BAZA_FRAMEWORK_DIR}/venv/bin/python" \
            "${BAZA_FRAMEWORK_DIR}/agents/specter_voss/openclaw/tools/baza_bridge.py" log_scan "$ARGS"
        ;;
    upgrade)
        # Stealth upgrade: specter upgrade deploy_code
        shift
        exec "${BAZA_FRAMEWORK_DIR}/venv/bin/python" \
            "${BAZA_FRAMEWORK_DIR}/agents/specter_voss/openclaw/tools/baza_bridge.py" "stealth_${1:-deploy}" "${2:-\{\}}"
        ;;
    skills)
        # List all skills: specter skills
        exec "${BAZA_FRAMEWORK_DIR}/venv/bin/python" \
            "${BAZA_FRAMEWORK_DIR}/agents/specter_voss/openclaw/tools/baza_bridge.py" list
        ;;
    chat|"")
        # Interactive OpenClaw chat: specter or specter chat
        exec openclaw
        ;;
    *)
        # One-shot to OpenClaw: specter "search for PA roofing permits"
        exec openclaw "$@"
        ;;
esac
LAUNCHEREOF

sudo chmod +x /usr/local/bin/specter
echo "  Installed /usr/local/bin/specter"
echo ""
echo "  Commands:"
echo "    specter              — OpenClaw interactive chat"
echo "    specter scan         — Infrastructure health check"
echo "    specter pulse        — All agent status"
echo "    specter pulse claw   — Deep dive on Claw"
echo "    specter logs         — Service log scan"
echo "    specter skills       — List all skills"
echo "    specter run <skill>  — Run any skill"
echo "    specter upgrade      — Stealth upgrade (approval-gated)"
echo "    specter \"query\"      — One-shot to OpenClaw"

# ── 6. Create systemd services ───────────────────────────────────────────────
echo ""
echo "[6/7] Installing systemd services..."

# Specter Telegram bot agent
sudo tee /etc/systemd/system/baza-specter.service > /dev/null << SVCEOF
[Unit]
Description=Baza Empire — Specter Voss (Telegram Agent)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${USER}
WorkingDirectory=${BAZA_DIR}
EnvironmentFile=${HOME}/baza-empire/.env.nuc
ExecStart=${BAZA_DIR}/venv/bin/python main.py specter
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVCEOF

# Specter watchdog — periodic health checks
sudo tee /etc/systemd/system/baza-specter-watchdog.service > /dev/null << WDEOF
[Unit]
Description=Baza Empire — Specter Watchdog (periodic scans)

[Service]
Type=oneshot
User=${USER}
WorkingDirectory=${BAZA_DIR}
EnvironmentFile=${HOME}/baza-empire/.env.nuc
ExecStart=${BAZA_DIR}/venv/bin/python ${OPENCLAW_DIR}/tools/baza_bridge.py baza_scan
WDEOF

sudo tee /etc/systemd/system/baza-specter-watchdog.timer > /dev/null << TIMEREOF
[Unit]
Description=Specter Watchdog Timer — scan every 30 min

[Timer]
OnBootSec=5min
OnUnitActiveSec=30min
Persistent=true

[Install]
WantedBy=timers.target
TIMEREOF

sudo systemctl daemon-reload
echo "  Created baza-specter.service"
echo "  Created baza-specter-watchdog.timer (every 30 min)"

# ── 7. Setup SSH key for main server access ──────────────────────────────────
echo ""
echo "[7/7] SSH key setup..."
if [ ! -f "${HOME}/.ssh/id_ed25519" ]; then
    echo "  Generating SSH key..."
    ssh-keygen -t ed25519 -f "${HOME}/.ssh/id_ed25519" -N "" -C "specter@phantom"
    echo ""
    echo "  >>> Copy this key to the main server:"
    echo "  ssh-copy-id -i ~/.ssh/id_ed25519.pub switchhacker@100.127.118.103"
    echo "  >>> Or manually add to ~/.ssh/authorized_keys on baza"
    echo ""
    cat "${HOME}/.ssh/id_ed25519.pub"
    echo ""
    echo "  >>> Press ENTER after copying the key..."
    read -r
else
    echo "  SSH key already exists"
fi

# Test SSH
echo "  Testing SSH to main server..."
if ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no switchhacker@${BAZA_MAIN_HOST:-100.127.118.103} "echo 'SSH OK'" 2>/dev/null; then
    echo "  SSH connection: OK"
else
    echo "  SSH connection: FAILED (set up key manually)"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  OpenClaw + Specter Voss Integration Complete!"
echo "═══════════════════════════════════════════════════════════"
echo ""
echo "  START SERVICES:"
echo "    sudo systemctl enable --now baza-specter"
echo "    sudo systemctl enable --now baza-specter-watchdog.timer"
echo ""
echo "  USE SPECTER:"
echo "    specter              Interactive OpenClaw chat"
echo "    specter scan         Full infra health check"
echo "    specter pulse        Agent status dashboard"
echo "    specter logs         Service log analysis"
echo "    specter skills       List all 50+ skills"
echo "    specter run <name>   Run any skill directly"
echo "    specter upgrade      Stealth upgrade (needs Serge approval)"
echo ""
echo "  TELEGRAM:"
echo "    Message @specter_voss_bot on Telegram"
echo "═══════════════════════════════════════════════════════════"
