#!/usr/bin/env python3
"""
Skill: hw_temps
Monitor hardware temperatures from BOTH phantom (local) and baza (remote SSH).

Returns CPU cores, GPU temps (AMD + NVIDIA), disk temps, thermal zones.
Flags anything above thresholds as an alert.

Usage:
    SKILL_ARGS='{}'                        # both nodes, summary format
    SKILL_ARGS='{"target":"phantom"}'      # only this node
    SKILL_ARGS='{"target":"baza"}'         # only baza via SSH
    SKILL_ARGS='{"format":"json"}'         # raw JSON
    SKILL_ARGS='{"format":"alert"}'        # only alerts (empty = all green)
"""
import os
import sys
import json
import subprocess
import socket
from datetime import datetime

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
target = args.get("target", "both")
fmt = args.get("format", "summary")

# Thresholds (°C)
WARN_CPU = 75
CRIT_CPU = 85
WARN_GPU = 80
CRIT_GPU = 90
WARN_DISK = 55
CRIT_DISK = 65

BAZA_HOST = os.environ.get("BAZA_MAIN_HOST", "100.127.118.103")
BAZA_USER = os.environ.get("BAZA_MAIN_USER", "switchhacker")


def run(cmd, timeout=10, ssh_host=None):
    """Run a command locally or via SSH."""
    if ssh_host:
        cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5",
               f"{BAZA_USER}@{ssh_host}", cmd]
        shell = False
    else:
        shell = True
    try:
        proc = subprocess.run(cmd, shell=shell, capture_output=True, text=True, timeout=timeout)
        return proc.stdout.strip() if proc.returncode == 0 else ""
    except Exception:
        return ""


def collect_node(hostname, ssh_host=None):
    """Collect all temp data from a node."""
    data = {"hostname": hostname, "cpu": {}, "gpu": {}, "disk": {}, "thermal": {}, "alerts": []}

    # CPU via sensors (if installed)
    sensors_out = run("sensors 2>/dev/null || lm-sensors 2>/dev/null", ssh_host=ssh_host)
    if sensors_out:
        current_chip = None
        for line in sensors_out.split("\n"):
            line = line.rstrip()
            if not line or line.startswith(" "):
                pass
            elif ":" not in line and line and not line.startswith("Adapter"):
                current_chip = line.strip()
                continue
            # Extract temperature
            if "°C" in line and ":" in line:
                label, rest = line.split(":", 1)
                label = label.strip()
                try:
                    temp = float(rest.strip().split("°")[0].strip().lstrip("+"))
                    key = f"{current_chip}:{label}" if current_chip else label
                    if "core" in label.lower() or "package" in label.lower() or "cpu" in (current_chip or "").lower() or "tctl" in label.lower() or "tdie" in label.lower():
                        data["cpu"][key] = temp
                        if temp >= CRIT_CPU:
                            data["alerts"].append(f"🔴 {hostname} CPU {key}: {temp}°C CRITICAL")
                        elif temp >= WARN_CPU:
                            data["alerts"].append(f"🟡 {hostname} CPU {key}: {temp}°C warning")
                    elif "edge" in label.lower() or "junction" in label.lower() or "mem" in label.lower() and "mhz" not in label.lower():
                        data["gpu"][key] = temp
                        if temp >= CRIT_GPU:
                            data["alerts"].append(f"🔴 {hostname} GPU {key}: {temp}°C CRITICAL")
                        elif temp >= WARN_GPU:
                            data["alerts"].append(f"🟡 {hostname} GPU {key}: {temp}°C warning")
                except (ValueError, IndexError):
                    pass

    # Thermal zones fallback
    tz_out = run("for z in /sys/class/thermal/thermal_zone*; do t=$(cat $z/type 2>/dev/null); v=$(cat $z/temp 2>/dev/null); [ -n \"$v\" ] && echo \"$(basename $z)|$t|$v\"; done", ssh_host=ssh_host)
    if tz_out:
        for line in tz_out.split("\n"):
            parts = line.split("|")
            if len(parts) == 3:
                zone, typ, raw = parts
                try:
                    temp = int(raw) / 1000.0
                    if temp > 0:  # skip -263 invalid readings
                        data["thermal"][f"{zone}({typ})"] = temp
                except ValueError:
                    pass

    # NVIDIA GPU
    nv = run("nvidia-smi --query-gpu=name,temperature.gpu,temperature.memory --format=csv,noheader 2>/dev/null", ssh_host=ssh_host)
    if nv:
        for i, line in enumerate(nv.split("\n")):
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2:
                name = parts[0]
                try:
                    temp = int(parts[1])
                    data["gpu"][f"NVIDIA-{i}:{name}"] = temp
                    if temp >= CRIT_GPU:
                        data["alerts"].append(f"🔴 {hostname} GPU {name}: {temp}°C CRITICAL")
                    elif temp >= WARN_GPU:
                        data["alerts"].append(f"🟡 {hostname} GPU {name}: {temp}°C warning")
                except ValueError:
                    pass

    # AMD GPU via rocm-smi or via sensors (already parsed above)
    rocm = run("rocm-smi --showtemp 2>/dev/null | grep -E 'edge|Temperature'", ssh_host=ssh_host)
    if rocm:
        import re
        for match in re.finditer(r'(\d+\.?\d*)\s*(?:°?C|c)?', rocm):
            try:
                temp = float(match.group(1))
                if 20 < temp < 120:  # sanity check
                    data["gpu"][f"AMD-rocm"] = temp
                    break
            except ValueError:
                pass

    # NVMe disk temperature via smartctl
    nvme = run("for d in /dev/nvme?n1; do [ -b $d ] && sudo -n smartctl -A $d 2>/dev/null | grep -iE 'temperature_composite|composite|temperature:' | head -1 | awk -v d=$d '{print d\": \"$NF}'; done", ssh_host=ssh_host)
    if nvme:
        for line in nvme.split("\n"):
            if ":" in line:
                dev, val = line.split(":", 1)
                try:
                    temp = int("".join(c for c in val if c.isdigit()))
                    if 20 < temp < 100:
                        data["disk"][dev.strip()] = temp
                        if temp >= CRIT_DISK:
                            data["alerts"].append(f"🔴 {hostname} DISK {dev}: {temp}°C CRITICAL")
                        elif temp >= WARN_DISK:
                            data["alerts"].append(f"🟡 {hostname} DISK {dev}: {temp}°C warning")
                except ValueError:
                    pass

    return data


# ── Collect ───────────────────────────────────────────────────────────────────
nodes = []
local_host = socket.gethostname()

if target in ("both", local_host, "local", "phantom", "baza"):
    if target == "both" or target == "local" or target == local_host:
        nodes.append(collect_node(local_host))

if target in ("both", "baza"):
    if local_host != "baza":
        nodes.append(collect_node("baza", ssh_host=BAZA_HOST))

if target == "phantom" and local_host != "phantom":
    # running on baza, phantom not supported from this direction
    pass

# ── Output ────────────────────────────────────────────────────────────────────
if fmt == "json":
    print(json.dumps(nodes, indent=2))
elif fmt == "alert":
    all_alerts = [a for n in nodes for a in n.get("alerts", [])]
    if all_alerts:
        print("⚠️ TEMPERATURE ALERTS")
        for a in all_alerts:
            print(f"  {a}")
    else:
        print("✅ All temps green — no alerts")
else:
    # Summary format
    print(f"🌡 HARDWARE TEMPERATURES — {datetime.now().strftime('%H:%M:%S')}")
    for n in nodes:
        print(f"\n═══ {n['hostname'].upper()} ═══")
        if n["cpu"]:
            print("  🖥  CPU:")
            for k, v in sorted(n["cpu"].items()):
                icon = "🔴" if v >= CRIT_CPU else ("🟡" if v >= WARN_CPU else "🟢")
                print(f"     {icon} {k:30.30}: {v:.0f}°C")
        if n["gpu"]:
            print("  🎮 GPU:")
            for k, v in sorted(n["gpu"].items()):
                icon = "🔴" if v >= CRIT_GPU else ("🟡" if v >= WARN_GPU else "🟢")
                print(f"     {icon} {k:30.30}: {v:.0f}°C")
        if n["disk"]:
            print("  💾 DISK:")
            for k, v in sorted(n["disk"].items()):
                icon = "🔴" if v >= CRIT_DISK else ("🟡" if v >= WARN_DISK else "🟢")
                print(f"     {icon} {k:30.30}: {v:.0f}°C")
        if n["thermal"] and not n["cpu"]:
            print("  🌡  Thermal zones:")
            for k, v in sorted(n["thermal"].items()):
                print(f"     • {k:30.30}: {v:.0f}°C")
        if n["alerts"]:
            print("  ⚠️ ALERTS:")
            for a in n["alerts"]:
                print(f"     {a}")
