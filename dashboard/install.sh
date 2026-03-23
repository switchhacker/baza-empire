#!/bin/bash
set -e

echo "=== Installing Baza Empire Dashboard ==="

# Install Flask if not present
cd ~/baza-empire/agent-framework-v3
source venv/bin/activate
pip install flask --quiet

# Allow dashboard to restart/stop agents without password
echo "switchhacker ALL=(ALL) NOPASSWD: /bin/systemctl restart baza-agent-*, /bin/systemctl stop baza-agent-*" | sudo tee /etc/sudoers.d/baza-dashboard > /dev/null

# Install and start service
sudo cp dashboard/baza-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable baza-dashboard
sudo systemctl restart baza-dashboard

echo ""
echo "✅ Dashboard running at http://localhost:8888"
echo "   Tailscale: http://100.127.118.103:8888"
