#!/usr/bin/env python3
"""Check backup status and last backup time."""
import os, json, glob
from datetime import datetime

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
backup_dirs = args.get("dirs", [
    "/home/switchhacker/backups",
    "/home/switchhacker/baza-empire/backups",
    "/var/backups"
])

results = []
for d in backup_dirs:
    if not os.path.isdir(d):
        results.append({"dir": d, "exists": False})
        continue
    files = sorted(glob.glob(os.path.join(d, "*")), key=os.path.getmtime, reverse=True)
    latest = None
    if files:
        f = files[0]
        mtime = datetime.fromtimestamp(os.path.getmtime(f))
        age_hours = round((datetime.now() - mtime).total_seconds() / 3600, 1)
        latest = {"file": os.path.basename(f), "time": mtime.isoformat(),
                   "size_mb": round(os.path.getsize(f) / (1024*1024), 1), "age_hours": age_hours}
    results.append({"dir": d, "exists": True, "file_count": len(files), "latest": latest})

print(json.dumps({"backups": results}))
