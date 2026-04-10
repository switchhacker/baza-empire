#!/bin/bash
# Ollama Watchdog — kills stuck runners + zombie serves
# Runs every 5 minutes via systemd timer
# A "stuck" runner is one consuming >200% CPU for more than 8 minutes straight
set -euo pipefail

LOG=/var/log/ollama-watchdog.log
mkdir -p "$(dirname "$LOG")"
now=$(date -Is)

# 1. Kill zombie ollama serve processes (keep only the newest)
serves=$(pgrep -f "ollama serve" | sort -n)
serve_count=$(echo "$serves" | wc -l)
if [ "$serve_count" -gt 1 ]; then
    # Keep only the most recent one
    newest=$(echo "$serves" | tail -1)
    for pid in $serves; do
        if [ "$pid" != "$newest" ]; then
            echo "$now [ZOMBIE] Killing duplicate ollama serve PID $pid" >> "$LOG"
            kill -9 "$pid" 2>/dev/null || true
        fi
    done
fi

# 2. Find ollama runner processes with high CPU and long runtime
while IFS= read -r line; do
    [ -z "$line" ] && continue
    pid=$(echo "$line" | awk '{print $1}')
    cpu=$(echo "$line" | awk '{print $2}')
    elapsed_str=$(echo "$line" | awk '{print $3}')
    cmd=$(echo "$line" | awk '{for(i=4;i<=NF;i++) printf "%s ",$i; print ""}')

    # Parse elapsed time — formats: MM:SS, HH:MM:SS, or D-HH:MM:SS
    if [[ "$elapsed_str" =~ ^([0-9]+)-([0-9]+):([0-9]+):([0-9]+)$ ]]; then
        elapsed_sec=$((BASH_REMATCH[1]*86400 + BASH_REMATCH[2]*3600 + BASH_REMATCH[3]*60 + BASH_REMATCH[4]))
    elif [[ "$elapsed_str" =~ ^([0-9]+):([0-9]+):([0-9]+)$ ]]; then
        elapsed_sec=$((BASH_REMATCH[1]*3600 + BASH_REMATCH[2]*60 + BASH_REMATCH[3]))
    elif [[ "$elapsed_str" =~ ^([0-9]+):([0-9]+)$ ]]; then
        elapsed_sec=$((BASH_REMATCH[1]*60 + BASH_REMATCH[2]))
    else
        continue
    fi

    # Kill if: >200% CPU AND running >8 minutes (480 sec)
    cpu_int="${cpu%.*}"
    if [ "$cpu_int" -gt 200 ] && [ "$elapsed_sec" -gt 480 ]; then
        echo "$now [STUCK] Killing ollama runner PID $pid (cpu=${cpu}% elapsed=${elapsed_sec}s)" >> "$LOG"
        kill -9 "$pid" 2>/dev/null || true
    fi
done < <(ps -eo pid,%cpu,etime,comm,args | grep "ollama runner" | grep -v grep | awk '{print $1, $2, $3, $5}')

exit 0
