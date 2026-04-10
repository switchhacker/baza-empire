#!/bin/bash
# Restart every Baza agent bot in one shot.
# Usage:  sudo ./scripts/restart-agents.sh
#         sudo ./scripts/restart-agents.sh phil   # just one
set -e
AGENTS=(simon-bately claw-batto phil-hass sam-axe duke-harmon rex-valor scout-reeves nova-sterling)
if [ -n "$1" ]; then
  systemctl restart "baza-agent-$1.service"
  echo "✓ restarted baza-agent-$1.service"
  systemctl status "baza-agent-$1.service" --no-pager -n 5
  exit 0
fi
for a in "${AGENTS[@]}"; do
  systemctl restart "baza-agent-$a.service"
  echo "✓ baza-agent-$a"
done
echo
echo "Status:"
for a in "${AGENTS[@]}"; do
  state=$(systemctl is-active "baza-agent-$a.service")
  printf "  %-22s %s\n" "$a" "$state"
done
