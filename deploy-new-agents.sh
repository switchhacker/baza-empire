#!/bin/bash
# Deploy Rex Valor, Duke Harmon, Scout Reeves, Nova Sterling
# Run from: ~/baza-empire/agent-framework-v3

set -e
FRAMEWORK_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_DIR="/etc/systemd/system"
VENV="$FRAMEWORK_DIR/venv"
PYTHON="$VENV/bin/python"
USER=$(whoami)

echo "==> Deploying new Baza Empire agents..."
echo "    Framework: $FRAMEWORK_DIR"
echo "    User: $USER"

# ── Service definitions ────────────────────────────────────────────────────────

create_service() {
  local AGENT_ID=$1
  local CLASS_NAME=$2
  local MODULE=$3
  local SERVICE_NAME="baza-agent-${AGENT_ID//_/-}"

  cat > "$SERVICE_DIR/$SERVICE_NAME.service" << EOF
[Unit]
Description=Baza Empire Agent — $CLASS_NAME
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$FRAMEWORK_DIR
EnvironmentFile=$FRAMEWORK_DIR/configs/secrets.env
ExecStart=$PYTHON -m agents.$MODULE.agent
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

  echo "    ✓ Service file: $SERVICE_NAME.service"
}

create_service "rex_valor"    "Rex Valor"    "rex_valor"
create_service "duke_harmon"  "Duke Harmon"  "duke_harmon"
create_service "scout_reeves" "Scout Reeves" "scout_reeves"
create_service "nova_sterling" "Nova Sterling" "nova_sterling"

# Also refresh Simon with latest agent.py
create_service "simon_bately" "Simon Bately" "simon_bately"

# ── Reload and enable ─────────────────────────────────────────────────────────

echo "==> Reloading systemd..."
systemctl daemon-reload

for AGENT in rex_valor duke_harmon scout_reeves nova_sterling; do
  SERVICE="baza-agent-${AGENT//_/-}"
  echo "==> Enabling and starting $SERVICE..."
  systemctl enable "$SERVICE" --now
done

# Restart Simon with latest code
echo "==> Restarting Simon with latest code..."
systemctl restart baza-agent-simon-bately

echo ""
echo "==> Status check:"
for AGENT in simon_bately rex_valor duke_harmon scout_reeves nova_sterling; do
  SERVICE="baza-agent-${AGENT//_/-}"
  STATUS=$(systemctl is-active "$SERVICE" 2>/dev/null || echo "unknown")
  echo "    $SERVICE: $STATUS"
done

echo ""
echo "Done. Full team deployed."
