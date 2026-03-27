#!/bin/bash
# Baza Empire — Update systemd to use SD WebUI Forge
# Usage: sudo bash scripts/setup-forge.sh
set -e

FORGE_DIR="/home/switchhacker/stable-diffusion-webui-forge"
USER="switchhacker"

echo "=== Updating baza-sd-webui.service to use Forge ==="
cat > /etc/systemd/system/baza-sd-webui.service << EOF
[Unit]
Description=Baza Empire — Stable Diffusion WebUI Forge (API)
After=network.target

[Service]
Type=simple
User=$USER
Group=$USER
WorkingDirectory=$FORGE_DIR
Environment=HOME=/home/$USER
Environment=PATH=/usr/local/cuda/bin:/usr/bin:/bin:/home/$USER/.local/bin
Environment=PYTHONUNBUFFERED=1
Environment=PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:512
ExecStart=$FORGE_DIR/baza-forge-launch.sh
Restart=on-failure
RestartSec=15
TimeoutStartSec=600
StandardOutput=journal
StandardError=journal
KillSignal=SIGTERM

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
echo "Starting SD WebUI Forge..."
systemctl start baza-sd-webui
echo ""
echo "First boot installs deps (~10 min). Watch with:"
echo "  journalctl -u baza-sd-webui -f"
echo "WebUI: http://localhost:7860"
