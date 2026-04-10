#!/usr/bin/env python3
"""Check disk usage across all drives."""
import os, json, shutil, subprocess

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
path = args.get("path", "/")

drives = []
try:
    result = subprocess.run(["df", "-h", "--output=source,size,used,avail,pcent,target"],
                          capture_output=True, text=True, timeout=10)
    for line in result.stdout.strip().split("\n")[1:]:
        parts = line.split()
        if len(parts) >= 6 and not parts[0].startswith("tmpfs"):
            drives.append({
                "device": parts[0], "size": parts[1], "used": parts[2],
                "available": parts[3], "use_pct": parts[4], "mount": parts[5]
            })
except Exception:
    total, used, free = shutil.disk_usage(path)
    drives.append({
        "path": path, "total_gb": round(total / (1024**3), 1),
        "used_gb": round(used / (1024**3), 1), "free_gb": round(free / (1024**3), 1),
        "use_pct": f"{round(used/total*100, 1)}%"
    })

print(json.dumps({"drives": drives}))
