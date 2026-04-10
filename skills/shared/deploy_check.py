#!/usr/bin/env python3
"""Pre-deployment checklist (services up, disk space, etc.)."""
import os, json, shutil, socket, subprocess

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
checks = []

# Disk space
total, used, free = shutil.disk_usage("/")
free_gb = round(free / (1024**3), 1)
checks.append({"check": "disk_space", "pass": free_gb > 5, "detail": f"{free_gb}GB free"})

# Critical services
for svc in ["postgresql", "redis", "ollama"]:
    try:
        r = subprocess.run(["systemctl", "is-active", svc], capture_output=True, text=True, timeout=5)
        active = r.stdout.strip() == "active"
        checks.append({"check": f"service_{svc}", "pass": active, "detail": r.stdout.strip()})
    except Exception:
        checks.append({"check": f"service_{svc}", "pass": False, "detail": "check failed"})

# Critical ports
for port, name in [(5432, "postgres"), (6379, "redis"), (11434, "ollama_amd"), (11435, "ollama_nvidia")]:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2)
    result = s.connect_ex(("localhost", port))
    checks.append({"check": f"port_{name}", "pass": result == 0, "detail": f"port {port}"})
    s.close()

# Load average
load1 = os.getloadavg()[0]
checks.append({"check": "load_avg", "pass": load1 < os.cpu_count() * 2, "detail": f"{load1}"})

all_pass = all(c["pass"] for c in checks)
print(json.dumps({"ready": all_pass, "checks": checks}))
