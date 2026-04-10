#!/usr/bin/env python3
"""Get system uptime and load averages."""
import os, json

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))

try:
    with open("/proc/uptime") as f:
        uptime_sec = float(f.read().split()[0])
    days = int(uptime_sec // 86400)
    hours = int((uptime_sec % 86400) // 3600)
    minutes = int((uptime_sec % 3600) // 60)

    load1, load5, load15 = os.getloadavg()
    cpu_count = os.cpu_count() or 1

    with open("/proc/meminfo") as f:
        mem = {}
        for line in f:
            parts = line.split()
            if parts[0] in ("MemTotal:", "MemAvailable:", "MemFree:"):
                mem[parts[0].rstrip(":")] = int(parts[1])

    print(json.dumps({
        "uptime": f"{days}d {hours}h {minutes}m",
        "uptime_seconds": round(uptime_sec),
        "load_avg": {"1min": round(load1, 2), "5min": round(load5, 2), "15min": round(load15, 2)},
        "cpu_count": cpu_count,
        "load_per_cpu": round(load1 / cpu_count, 2),
        "memory": {
            "total_mb": round(mem.get("MemTotal", 0) / 1024),
            "available_mb": round(mem.get("MemAvailable", 0) / 1024),
            "used_pct": round((1 - mem.get("MemAvailable", 0) / max(mem.get("MemTotal", 1), 1)) * 100, 1)
        }
    }))
except Exception as e:
    print(json.dumps({"error": str(e)}))
