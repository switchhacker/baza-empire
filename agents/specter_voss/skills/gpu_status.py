#!/usr/bin/env python3
"""
Skill: gpu_status
Shows the live GPU pool — which agent is on which backend, temperatures,
VRAM usage, and any warnings. Specter calls this to know how baza's hardware
is being utilized right now.

Reads via SSH from baza (pool lives on the main server, not phantom).

Usage:
    SKILL_ARGS='{}'
"""
import os
import subprocess
import json
import sys

BAZA_HOST = os.environ.get("BAZA_MAIN_HOST", "100.127.118.103")
BAZA_USER = os.environ.get("BAZA_MAIN_USER", "switchhacker")

# Run remotely on baza so we get live pool state from where the agents live
remote_cmd = '''
cd /home/switchhacker/baza-empire/agent-framework-v3 && \
./venv/bin/python -c "
import json
from core.gpu_pool import gpu_pool
print(json.dumps(gpu_pool.status()))
"
'''

try:
    proc = subprocess.run(
        ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5",
         f"{BAZA_USER}@{BAZA_HOST}", remote_cmd],
        capture_output=True, text=True, timeout=15,
    )
    if proc.returncode != 0:
        print(f"Error: {proc.stderr[:300]}")
        sys.exit(1)
    slots = json.loads(proc.stdout.strip().split("\n")[-1])
except Exception as e:
    print(f"Failed to query baza: {e}")
    sys.exit(1)

# Also fetch loaded models per backend
def get_loaded(port):
    try:
        proc = subprocess.run(
            ["ssh", "-o", "StrictHostKeyChecking=no",
             f"{BAZA_USER}@{BAZA_HOST}",
             f"curl -s --max-time 3 http://127.0.0.1:{port}/api/ps"],
            capture_output=True, text=True, timeout=8,
        )
        return json.loads(proc.stdout).get("models", [])
    except Exception:
        return []

PORT_MAP = {"http://127.0.0.1:11434": 11434,
            "http://127.0.0.1:11435": 11435,
            "http://127.0.0.1:11436": 11436}

print("🖥  GPU POOL STATUS")
print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
for s in slots:
    icon = {"cuda": "🟢", "vulkan": "🔴", "cpu": "⚪"}.get(s["backend"], "•")
    name = s["name"]
    backend = s["backend"].upper()
    vram = f"{s['vram_mb']/1024:.1f}GB" if s["vram_mb"] else "RAM"
    temp = s.get("temp")
    if temp is None:
        temp_str = "n/a"
        temp_icon = ""
    elif s["backend"] == "cpu":
        temp_str = "n/a"
        temp_icon = ""
    else:
        temp_str = f"{temp}°C"
        if temp >= s.get("temp_crit", 999):
            temp_icon = "🔴 CRIT"
        elif temp >= s.get("temp_warn", 999):
            temp_icon = "🟡 warn"
        else:
            temp_icon = "🟢"

    print(f"\n{icon} {name}  ({backend}, {vram})")
    print(f"   Temperature: {temp_str} {temp_icon}")
    print(f"   In use: {'YES (' + (s.get('agent') or '?') + ')' if s['in_use'] else 'free'}")
    port = PORT_MAP.get(s["url"])
    if port:
        loaded = get_loaded(port)
        if loaded:
            print("   Loaded models:")
            for m in loaded:
                vram_used = m.get("size_vram", 0) // 1024 // 1024
                print(f"     • {m['name']:30} {vram_used}MB VRAM")
        else:
            print("   Loaded models: (none)")

print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
hot = [s for s in slots if s.get("temp") and s.get("temp_warn", 999) < 999 and s["temp"] >= s["temp_warn"]]
if hot:
    print(f"⚠️  {len(hot)} GPU(s) at warning temperature — pool will steer load away")
else:
    print("✅ All GPUs nominal")
