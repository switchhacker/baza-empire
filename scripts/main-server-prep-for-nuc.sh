#!/bin/bash
set -euo pipefail
# ═══════════════════════════════════════════════════════════════════════════════
# Baza Empire — Main Server Prep for NUC Connection
# Run this on the MAIN SERVER (baza) to allow NUC to connect
# ═══════════════════════════════════════════════════════════════════════════════

echo "═══════════════════════════════════════════"
echo "  MAIN SERVER — NUC Access Setup"
echo "═══════════════════════════════════════════"

# ── 1. Install Tailscale (if not already) ─────────────────────────────────────
echo ""
echo "[1/4] Checking Tailscale..."
if command -v tailscale &>/dev/null; then
    MAIN_IP=$(tailscale ip -4 2>/dev/null || echo "not connected")
    echo "  Tailscale IP: $MAIN_IP"
else
    echo "  Installing Tailscale..."
    curl -fsSL https://tailscale.com/install.sh | sh
    echo "  >>> Run: sudo tailscale up"
    echo "  >>> Press ENTER when done..."
    read -r
    MAIN_IP=$(tailscale ip -4)
    echo "  Tailscale IP: $MAIN_IP"
fi

# ── 2. PostgreSQL — Allow remote connections ──────────────────────────────────
echo ""
echo "[2/4] Configuring PostgreSQL for remote access..."

PG_CONF=$(sudo -u postgres psql -t -c "SHOW config_file;" 2>/dev/null | tr -d ' ')
PG_HBA=$(sudo -u postgres psql -t -c "SHOW hba_file;" 2>/dev/null | tr -d ' ')

if [ -n "$PG_CONF" ]; then
    echo "  postgresql.conf: $PG_CONF"

    # Check if listen_addresses is already set to allow remote
    if grep -q "^listen_addresses.*=.*'\*'" "$PG_CONF" 2>/dev/null; then
        echo "  listen_addresses already set to '*'"
    else
        echo "  Setting listen_addresses = '*'"
        sudo sed -i "s/^#\?listen_addresses.*/listen_addresses = '*'/" "$PG_CONF"
    fi

    # Add Tailscale subnet to pg_hba.conf
    if grep -q "100.0.0.0/8" "$PG_HBA" 2>/dev/null; then
        echo "  Tailscale subnet already in pg_hba.conf"
    else
        echo "  Adding Tailscale subnet to pg_hba.conf"
        echo "# Baza NUC access via Tailscale" | sudo tee -a "$PG_HBA" >/dev/null
        echo "host    baza_agents    switchhacker    100.0.0.0/8    md5" | sudo tee -a "$PG_HBA" >/dev/null
    fi

    echo "  Restarting PostgreSQL..."
    sudo systemctl restart postgresql
    echo "  PostgreSQL configured for remote access"
else
    echo "  WARNING: Could not find postgresql.conf — configure manually"
fi

# ── 3. Redis — Allow remote connections ───────────────────────────────────────
echo ""
echo "[3/4] Configuring Redis for remote access..."

REDIS_CONF="/etc/redis/redis.conf"
if [ -f "$REDIS_CONF" ]; then
    # Bind to all interfaces (Tailscale is encrypted already)
    if grep -q "^bind 0.0.0.0" "$REDIS_CONF" 2>/dev/null; then
        echo "  Redis already bound to 0.0.0.0"
    else
        echo "  Setting Redis bind to 0.0.0.0"
        sudo sed -i 's/^bind 127.0.0.1.*/bind 0.0.0.0/' "$REDIS_CONF"
    fi

    # Set protected-mode off (Tailscale handles security)
    if grep -q "^protected-mode no" "$REDIS_CONF" 2>/dev/null; then
        echo "  protected-mode already off"
    else
        echo "  Setting protected-mode no"
        sudo sed -i 's/^protected-mode yes/protected-mode no/' "$REDIS_CONF"
    fi

    echo "  Restarting Redis..."
    sudo systemctl restart redis
    echo "  Redis configured for remote access"
else
    echo "  WARNING: Redis config not found at $REDIS_CONF — configure manually"
fi

# ── 4. Firewall — Allow Tailscale traffic ─────────────────────────────────────
echo ""
echo "[4/4] Checking firewall..."
if command -v ufw &>/dev/null; then
    # Allow from Tailscale subnet
    sudo ufw allow from 100.0.0.0/8 to any port 5432 comment "PostgreSQL from NUC" 2>/dev/null || true
    sudo ufw allow from 100.0.0.0/8 to any port 6379 comment "Redis from NUC" 2>/dev/null || true
    sudo ufw allow from 100.0.0.0/8 to any port 4000 comment "LiteLLM from NUC" 2>/dev/null || true
    sudo ufw allow from 100.0.0.0/8 to any port 8888 comment "Dashboard from NUC" 2>/dev/null || true
    echo "  UFW rules added for Tailscale subnet"
else
    echo "  No UFW found — ensure your firewall allows Tailscale (100.0.0.0/8)"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  Main Server Ready for NUC!"
echo "═══════════════════════════════════════════════════════════"
echo ""
echo "  Main Server Tailscale IP: ${MAIN_IP:-unknown}"
echo ""
echo "  Use this IP in the NUC's .env.nuc file:"
echo "    BAZA_DB_HOST=${MAIN_IP:-100.x.y.z}"
echo "    BAZA_REDIS_HOST=${MAIN_IP:-100.x.y.z}"
echo "    LITELLM_URL=http://${MAIN_IP:-100.x.y.z}:4000"
echo ""
echo "  Test from NUC:"
echo "    psql -h ${MAIN_IP:-100.x.y.z} -U switchhacker -d baza_agents"
echo "    redis-cli -h ${MAIN_IP:-100.x.y.z} ping"
echo "═══════════════════════════════════════════════════════════"
