#!/usr/bin/env python3
"""List artifacts from the dashboard artifacts directory."""
import os, json, glob

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../dashboard/artifacts")
project = args.get("project", "")

if project:
    pattern = os.path.join(base, project, "*")
else:
    pattern = os.path.join(base, "*", "*")

files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
artifacts = []
for f in files[:args.get("limit", 50)]:
    artifacts.append({
        "path": os.path.relpath(f, base),
        "name": os.path.basename(f),
        "size": os.path.getsize(f),
        "modified": os.path.getmtime(f),
    })

print(json.dumps({"count": len(artifacts), "artifacts": artifacts}))
