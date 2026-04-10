#!/usr/bin/env python3
"""Backup SQLite database to a timestamped file."""
import os, json, shutil
from datetime import datetime
args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "dashboard", "baza_projects.db")
backup_dir = args.get("dir", "/tmp")
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
dest = os.path.join(backup_dir, f"baza_projects_backup_{ts}.db")
shutil.copy2(DB, dest)
print(json.dumps({"backup": dest, "size": os.path.getsize(dest), "timestamp": ts}))
