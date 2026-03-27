#!/bin/bash
echo "=== Mining Status ==="
for svc in baza-mining baza-trex baza-teamred; do
    status=$(systemctl is-active ${svc}.service 2>/dev/null || echo "not-found")
    echo "  $svc: $status"
done
echo ""
if pgrep -x xmrig > /dev/null; then
    echo "XMRig: RUNNING (PID: $(pgrep -x xmrig))"
    curl -s --max-time 3 http://localhost:18083/api.json 2>/dev/null | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    hr=d.get('hashrate',{}).get('total',[0])[0]
    print(f'  Hashrate: {hr:.1f} H/s')
    print(f'  Uptime: {d.get(\"uptime\",0)//3600}h {(d.get(\"uptime\",0)%3600)//60}m')
except: pass
" 2>/dev/null
else
    echo "XMRig: NOT RUNNING"
fi
