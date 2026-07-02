#!/usr/bin/env bash
# Claw Batto — weekly log rotation for logs/*.log.
# Uses an unprivileged, framework-local state file (--state) so this can run
# as `switchhacker` via cron without touching the system logrotate state at
# /var/lib/logrotate/status. See configs/logrotate-baza.conf for the rule
# (weekly, rotate 8, compress, copytruncate, missingok, notifempty).
set -euo pipefail

FW_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_FILE="$FW_DIR/logs/.logrotate.state"
CONF_FILE="$FW_DIR/configs/logrotate-baza.conf"

LOGROTATE_BIN="/usr/sbin/logrotate"
if [ ! -x "$LOGROTATE_BIN" ]; then
    LOGROTATE_BIN="$(command -v logrotate || true)"
fi

if [ -z "$LOGROTATE_BIN" ]; then
    echo "rotate_logs.sh: logrotate not found (checked /usr/sbin/logrotate and PATH)" >&2
    exit 1
fi

mkdir -p "$FW_DIR/logs"

exec "$LOGROTATE_BIN" --state "$STATE_FILE" "$CONF_FILE"
