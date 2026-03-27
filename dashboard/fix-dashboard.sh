#!/bin/bash
# Fix and restart the Baza dashboard with correct paths

FRAMEWORK_DIR="/home/switchhacker/baza-empire/agent-framework-v3"
VENV="$FRAMEWORK_DIR/venv"

echo "==> Checking dashboard service file..."
cat /etc/systemd/system/baza-dashboard.service

echo ""
echo "==> Checking which agents.yaml the dashboard sees..."
python3 -c "
import os, yaml
config_path = os.path.join('$FRAMEWORK_DIR', 'config', 'agents.yaml')
print('Config path:', config_path)
print('Exists:', os.path.exists(config_path))
with open(config_path) as f:
    config = yaml.safe_load(f)
agents = config.get('agents', {})
print('Agents found:', list(agents.keys()))
"

echo ""
echo "==> Rewriting dashboard service with correct path..."
cat > /etc/systemd/system/baza-dashboard.service << EOF
[Unit]
Description=Baza Empire Dashboard
After=network.target

[Service]
Type=simple
User=switchhacker
WorkingDirectory=$FRAMEWORK_DIR/dashboard
Environment=PYTHONPATH=$FRAMEWORK_DIR
ExecStart=$VENV/bin/python app.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

echo "==> Reloading and restarting..."
systemctl daemon-reload
systemctl restart baza-dashboard
sleep 3
systemctl status baza-dashboard --no-pager | grep -E "Active|Error|python"
echo ""
echo "Done. Check http://100.127.118.103:8888/"
