#!/bin/bash
echo "=== System Health === $(date)"
echo "--- CPU ---"
echo "Load: $(uptime | awk -F'load average:' '{print $2}')"
echo "--- Memory ---"
free -h | grep -E "Mem|Swap"
echo "--- ZFS ---"
df -h /mnt/empirepool 2>/dev/null || echo "ZFS not mounted"
echo "--- GPU ---"
nvidia-smi --query-gpu=name,temperature.gpu,utilization.gpu --format=csv,noheader 2>/dev/null || echo "NVIDIA: n/a"
echo "--- Services ---"
for svc in baza-mining baza-dashboard ollama docker postgresql; do
    echo "  $svc: $(systemctl is-active ${svc}.service 2>/dev/null || echo unknown)"
done
