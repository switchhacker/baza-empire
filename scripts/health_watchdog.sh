#!/usr/bin/env bash
# Baza Empire — Service Watchdog
# Monitors critical services and restarts them if dead. Alerts Simon via Telegram
# on repeated failures (3 restarts in 60min).
#
# Triggered by baza-watchdog.timer every 5 minutes.
set -uo pipefail

FRAMEWORK=/home/switchhacker/baza-empire/agent-framework-v3
STATE=/home/switchhacker/baza-empire/agent-framework-v3/logs/watchdog.state
LOG=/home/switchhacker/baza-empire/agent-framework-v3/logs/watchdog.log
mkdir -p "$(dirname "$STATE")"
touch "$STATE"

# shellcheck disable=SC1091
[ -f "$FRAMEWORK/configs/secrets.env" ] && . "$FRAMEWORK/configs/secrets.env"

CRITICAL_SERVICES=(
    baza-agent-simon-bately.service
    baza-agent-claw-batto.service
    baza-agent-phil-hass.service
    baza-agent-duke-harmon.service
    baza-agent-sam-axe.service
    baza-agent-rex-valor.service
    baza-agent-scout-reeves.service
    baza-agent-nova-sterling.service
    baza-dashboard.service
    baza-tool-server.service
    baza-litellm.service
    postgresql.service
    baza-phantom-browser.service
)

NOW=$(date +%s)
WINDOW=3600   # 1h window for repeated-failure alerting
MAX_RESTARTS=3

log() { echo "[$(date -Iseconds)] $*" >> "$LOG"; }

# Prune state entries older than 2× window
awk -v cutoff=$((NOW - 2*WINDOW)) '$2 >= cutoff' "$STATE" > "$STATE.tmp" && mv "$STATE.tmp" "$STATE"

restarted=()
alerted=""
for svc in "${CRITICAL_SERVICES[@]}"; do
    state=$(systemctl is-active "$svc" 2>/dev/null || true)
    if [ "$state" = "active" ]; then continue; fi
    # Try to restart
    log "unhealthy: $svc (state=$state) — restarting"
    if sudo -n systemctl restart "$svc" 2>>"$LOG"; then
        echo "$svc $NOW" >> "$STATE"
        restarted+=("$svc")
        sleep 2
        new_state=$(systemctl is-active "$svc" 2>/dev/null || true)
        log "restart result for $svc: $new_state"
    else
        log "restart failed for $svc"
    fi
done

# Count restarts per service within the window — escalate only when the
# service is STILL not healthy AND we restarted it this run AND repeated
# failures seen. A service that recovered doesn't keep generating alerts
# just because history within the window is above threshold.
escalations=""
for svc in "${CRITICAL_SERVICES[@]}"; do
    # did we restart this service in THIS run?
    restarted_this_run=0
    for r in "${restarted[@]}"; do
        [ "$r" = "$svc" ] && restarted_this_run=1
    done
    [ "$restarted_this_run" = "0" ] && continue
    # is it still broken?
    now_state=$(systemctl is-active "$svc" 2>/dev/null || true)
    [ "$now_state" = "active" ] && continue
    count=$(awk -v s="$svc" -v cutoff=$((NOW - WINDOW)) '$1 == s && $2 >= cutoff' "$STATE" | wc -l)
    if [ "$count" -ge "$MAX_RESTARTS" ]; then
        escalations+="$svc ($count restarts in ${WINDOW}s); "
    fi
done

# Alert via Simon's Telegram if we escalated (uses existing send_telegram skill if available)
if [ -n "$escalations" ] && [ -n "${TELEGRAM_SIMON_BATELY:-}" ] && [ -n "${SERGE_CHAT_ID:-}" ]; then
    msg="⚠️ Baza Watchdog escalation: $escalations"
    curl -sS -m 10 "https://api.telegram.org/bot${TELEGRAM_SIMON_BATELY}/sendMessage" \
        -d "chat_id=${SERGE_CHAT_ID}" \
        -d "text=${msg}" >/dev/null 2>>"$LOG" || true
    log "alerted: $escalations"
fi

if [ ${#restarted[@]} -gt 0 ]; then
    log "restarted: ${restarted[*]}"
fi
