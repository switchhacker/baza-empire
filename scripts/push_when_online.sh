#!/usr/bin/env bash
# push_when_online.sh — Push any local commits to origin once internet is back.
#
# Usage:
#   push_when_online.sh                 # exits 0 if up-to-date or push succeeded
#   push_when_online.sh --watch         # poll every 60s until push lands, then exit
#
# Why this exists: Claude commits locally during offline sessions. This script
# is the trigger to release those commits. Drop into cron or run manually.
set -euo pipefail

REPO="/home/switchhacker/baza-empire/agent-framework-v3"
cd "$REPO"

attempt_push() {
  # 0 = up-to-date, 1 = pushed, 2 = no internet, 3 = push failed
  local ahead
  ahead="$(git rev-list --count @{u}..HEAD 2>/dev/null || echo 0)"
  if [[ "$ahead" == "0" ]]; then
    echo "[$(date -Is)] up-to-date — nothing to push"
    return 0
  fi
  echo "[$(date -Is)] $ahead commit(s) ahead of origin/$(git branch --show-current); attempting push"
  # Probe connectivity quickly so we don't burn cycles on git auth retries
  if ! curl -m 4 -s -o /dev/null https://github.com; then
    echo "[$(date -Is)] no internet (github.com unreachable)"
    return 2
  fi
  if git push origin "$(git branch --show-current)" 2>&1; then
    echo "[$(date -Is)] push succeeded"
    return 1
  else
    echo "[$(date -Is)] push failed (auth, hook, etc.)"
    return 3
  fi
}

if [[ "${1:-}" == "--watch" ]]; then
  while true; do
    set +e
    attempt_push
    rc=$?
    set -e
    if [[ "$rc" == "0" || "$rc" == "1" ]]; then
      exit 0
    fi
    sleep 60
  done
else
  set +e
  attempt_push
  rc=$?
  set -e
  # Treat "up-to-date" or "pushed" as success; "no internet" as deferred (exit 0
  # so cron doesn't email); "push failed" as a real error.
  case "$rc" in
    0|1|2) exit 0 ;;
    *)     exit "$rc" ;;
  esac
fi
