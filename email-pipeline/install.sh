#!/bin/bash
# Baza Email Pipeline — Install
# Sets up systemd timers for fetch (every 15 min) and summarize (every 15 min, offset 7 min)

set -e

VENV="/home/switchhacker/baza-empire/agent-framework-v3/venv"
PIPELINE_DIR="/home/switchhacker/baza-empire/agent-framework-v3/email-pipeline"
USER="switchhacker"

echo "[install] Installing Python dependencies..."
$VENV/bin/pip install --quiet google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client requests

echo "[install] Writing systemd service + timer: baza-email-fetch..."
cat > /etc/systemd/system/baza-email-fetch.service << EOF
[Unit]
Description=Baza Email Fetch — Pull new Gmail messages
After=network.target

[Service]
Type=oneshot
User=$USER
WorkingDirectory=$PIPELINE_DIR
ExecStart=$VENV/bin/python $PIPELINE_DIR/fetch_emails.py
StandardOutput=journal
StandardError=journal
EOF

cat > /etc/systemd/system/baza-email-fetch.timer << EOF
[Unit]
Description=Baza Email Fetch — every 15 min
Requires=baza-email-fetch.service

[Timer]
OnBootSec=2min
OnUnitActiveSec=15min
AccuracySec=30s

[Install]
WantedBy=timers.target
EOF

echo "[install] Writing systemd service + timer: baza-email-summarize..."
cat > /etc/systemd/system/baza-email-summarize.service << EOF
[Unit]
Description=Baza Email Summarize — Ollama summarize + Telegram notify
After=network.target

[Service]
Type=oneshot
User=$USER
WorkingDirectory=$PIPELINE_DIR
ExecStart=$VENV/bin/python $PIPELINE_DIR/summarize.py
StandardOutput=journal
StandardError=journal
EOF

cat > /etc/systemd/system/baza-email-summarize.timer << EOF
[Unit]
Description=Baza Email Summarize — every 15 min (offset 7 min)
Requires=baza-email-summarize.service

[Timer]
OnBootSec=9min
OnUnitActiveSec=15min
AccuracySec=30s

[Install]
WantedBy=timers.target
EOF

echo "[install] Reloading systemd..."
systemctl daemon-reload

echo "[install] Enabling + starting timers..."
systemctl enable baza-email-fetch.timer baza-email-summarize.timer
systemctl start  baza-email-fetch.timer baza-email-summarize.timer

echo ""
echo "✅ Email pipeline installed!"
echo ""
echo "Timer status:"
systemctl status baza-email-fetch.timer baza-email-summarize.timer --no-pager
echo ""
echo "Next: Copy your credentials.json to $PIPELINE_DIR/ then run:"
echo "  source $VENV/bin/activate && python $PIPELINE_DIR/gmail_auth.py"
echo ""
echo "To check logs:"
echo "  journalctl -u baza-email-fetch -f"
echo "  journalctl -u baza-email-summarize -f"
