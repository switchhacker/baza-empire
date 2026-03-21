#!/bin/bash
set -e

FRAMEWORK_DIR="/home/switchhacker/baza-empire/agent-framework-v3"
AGENTS=("simon_bately" "claw_batto" "phil_hass")

echo "=== Deploying Baza Agent Services ==="

for AGENT in "${AGENTS[@]}"; do
  SERVICE_NAME="baza-agent-${AGENT//_/-}"
  SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

  echo "Creating service: $SERVICE_NAME"

  sudo tee "$SERVICE_FILE" > /dev/null << EOF
[Unit]
Description=Baza Empire Agent - ${AGENT}
After=network.target ollama.service postgresql.service
Wants=ollama.service

[Service]
Type=simple
User=switchhacker
WorkingDirectory=${FRAMEWORK_DIR}
EnvironmentFile=/etc/baza-agents.env
ExecStart=/usr/bin/python3 ${FRAMEWORK_DIR}/agent.py --agent ${AGENT}
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

  sudo systemctl daemon-reload
  sudo systemctl enable "$SERVICE_NAME"
  sudo systemctl restart "$SERVICE_NAME"
  echo "✓ $SERVICE_NAME started"
done

echo ""
echo "=== All agents deployed ==="
echo "Check status: sudo systemctl status baza-agent-simon-bately"
echo "View logs:    sudo journalctl -fu baza-agent-simon-bately"
