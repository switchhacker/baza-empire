#!/bin/bash
set -e

echo "=== Baza Empire Agent Framework v3 Install ==="

# Install Python deps
pip3 install python-telegram-bot==20.7 pyyaml psycopg2-binary requests

# Create PostgreSQL database
echo "Setting up database..."
sudo -u postgres psql -c "CREATE DATABASE baza_agents;" 2>/dev/null || echo "DB already exists"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE baza_agents TO switchhacker;" 2>/dev/null || true

# Create env file
if [ ! -f /etc/baza-agents.env ]; then
cat > /etc/baza-agents.env << EOF
TELEGRAM_BRAD_GANT=YOUR_BRAD_TOKEN_HERE
TELEGRAM_SIMON_BATELY=8259565938:AAFCNLSrw096JALxvgmiBCkgByn0uDyGGMo
TELEGRAM_CLAW_BATTO=8767913900:AAGqzzTkpk14dF9hEUMR7sxsTeyvWVigktI
TELEGRAM_PHIL_HASS=8646880015:AAEJPvYChsyvXcJSEWFmkLQed8uEROMYKRI
EOF
echo "Created /etc/baza-agents.env — add Brad's token manually"
fi

echo "Done. Run ./deploy-services.sh to install systemd services."
