#!/bin/bash
# Install Claw CLI — makes 'claw' available system-wide
# Run: bash install_claw_cli.sh

set -euo pipefail

FRAMEWORK_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
CLI_SCRIPT="$FRAMEWORK_DIR/agents/claw_batto/claw_cli.py"
VENV_PYTHON="$FRAMEWORK_DIR/venv/bin/python"
LAUNCHER="/usr/local/bin/claw"

echo "📦 Installing Claw CLI..."
echo "  Framework: $FRAMEWORK_DIR"
echo "  Launcher:  $LAUNCHER"

# Create the launcher wrapper
sudo tee "$LAUNCHER" > /dev/null <<EOF
#!/bin/bash
# Claw Batto — Dev CLI Agent launcher
export PYTHONPATH="$FRAMEWORK_DIR"
export OLLAMA_HOST="\${OLLAMA_HOST:-http://localhost:11434}"
export CLAW_MODEL="\${CLAW_MODEL:-mistral-small:22b}"
cd "\${CLAW_CWD:-$FRAMEWORK_DIR}"
exec "$VENV_PYTHON" "$CLI_SCRIPT" "\$@"
EOF

sudo chmod +x "$LAUNCHER"
echo "✅ Installed: claw"
echo ""
echo "Usage:"
echo "  claw                          # interactive REPL"
echo "  claw 'fix the auth bug'       # one-shot task"
echo "  claw --file app.py 'refactor' # with file context"
echo "  claw --model qwen2.5:14b      # use different model"
echo ""

# Install infra report cron (runs as current user)
INFRA_SCRIPT="$FRAMEWORK_DIR/skills/shared/infra_report.py"
CRON_LINE="0 8 * * * $VENV_PYTHON $INFRA_SCRIPT >> $FRAMEWORK_DIR/logs/infra_report.log 2>&1"

echo "📅 Installing daily infra report cron (8am daily)..."
# Remove any existing infra_report cron line, add new one
(crontab -l 2>/dev/null | grep -v "infra_report"; echo "$CRON_LINE") | crontab -
echo "✅ Cron installed:"
echo "  $CRON_LINE"
echo ""
echo "Test run now:"
echo "  $VENV_PYTHON $INFRA_SCRIPT"
