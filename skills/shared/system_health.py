#!/usr/bin/env python3
"""
Baza Empire Skill — system_health
Reports CPU, memory, disk, and GPU stats from the local baza server.
Usage: ##SKILL:system_health{}##
"""
import os, json, subprocess

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))

def run(cmd):
    try:
        return subprocess.check_output(cmd, shell=True, text=True, timeout=8).strip()
    except Exception as e:
        return f"(unavailable: {e})"

lines = []
lines.append("⚙️ SYSTEM HEALTH — baza server")
lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")

# CPU
try:
    with open("/proc/loadavg") as f:
        load = f.read().split()
    cpu_1, cpu_5, cpu_15 = load[0], load[1], load[2]
    # CPU count for context
    cpu_count = run("nproc").strip()
    lines.append(f"🖥️  CPU Load: {cpu_1} / {cpu_5} / {cpu_15} (1/5/15min) | {cpu_count} cores")
except Exception as e:
    lines.append(f"🖥️  CPU: unavailable ({e})")

# Memory
try:
    mem = {}
    with open("/proc/meminfo") as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 2:
                mem[parts[0].rstrip(":")] = int(parts[1])
    total_gb = mem.get("MemTotal", 0) / 1024 / 1024
    avail_gb = mem.get("MemAvailable", 0) / 1024 / 1024
    used_gb = total_gb - avail_gb
    pct = (used_gb / total_gb * 100) if total_gb > 0 else 0
    lines.append(f"💾  Memory: {used_gb:.1f}GB used / {total_gb:.1f}GB total ({pct:.0f}%)")
except Exception as e:
    lines.append(f"💾  Memory: unavailable ({e})")

# Disk
try:
    df = run("df -h / | tail -1")
    parts = df.split()
    if len(parts) >= 5:
        lines.append(f"💿  Disk (/): {parts[2]} used / {parts[1]} total ({parts[4]} full)")
    # ZFS pool if available
    zfs = run("zpool list -H -o name,size,alloc,free,health 2>/dev/null | head -3")
    if zfs and "unavailable" not in zfs and zfs.strip():
        for zline in zfs.strip().split("\n"):
            p = zline.split()
            if len(p) >= 5:
                lines.append(f"🗄️  ZFS {p[0]}: {p[2]}/{p[1]} used | health: {p[4]}")
except Exception as e:
    lines.append(f"💿  Disk: unavailable ({e})")

# NVIDIA GPU
try:
    nv = run("nvidia-smi --query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total,power.draw "
             "--format=csv,noheader,nounits 2>/dev/null")
    if nv and "unavailable" not in nv and nv.strip():
        for gpu_line in nv.strip().split("\n"):
            p = [x.strip() for x in gpu_line.split(",")]
            if len(p) >= 6:
                lines.append(f"🟢  NVIDIA {p[0]}: {p[2]}% util | {p[3]}/{p[4]}MB VRAM | {p[1]}°C | {p[5]}W")
except Exception:
    pass

# AMD GPU
try:
    # Check if rocm-smi or radeontop available
    amd = run("rocm-smi --showuse --showtemp --showpower --csv 2>/dev/null | grep -v '^,' | tail -1")
    if amd and "unavailable" not in amd and len(amd.strip()) > 5:
        lines.append(f"🔴  AMD GPU (rocm-smi): {amd.strip()[:80]}")
    else:
        # Try reading clocks via sysfs
        clk = run("cat /sys/class/drm/card*/device/pp_dpm_sclk 2>/dev/null | grep '*' | head -1")
        temp = run("cat /sys/class/hwmon/hwmon*/temp1_input 2>/dev/null | head -1")
        if clk and temp and "unavailable" not in clk:
            temp_c = int(temp.strip()) // 1000 if temp.strip().isdigit() else "?"
            lines.append(f"🔴  AMD GPU: {clk.strip()} | {temp_c}°C")
except Exception:
    pass

# Uptime
try:
    uptime = run("uptime -p")
    lines.append(f"⏱️  Uptime: {uptime}")
except Exception:
    pass

# Running baza services
try:
    svcs = run("systemctl list-units 'baza-*' --state=running --no-legend --no-pager 2>/dev/null | awk '{print $1}'")
    if svcs and "unavailable" not in svcs and svcs.strip():
        svc_list = [s for s in svcs.strip().split("\n") if s.strip()]
        lines.append(f"🤖  Baza services running: {len(svc_list)}")
        for s in svc_list[:6]:
            lines.append(f"    ✅ {s}")
    else:
        lines.append("🤖  Baza services: none detected active")
except Exception:
    pass

lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
print("\n".join(lines))
