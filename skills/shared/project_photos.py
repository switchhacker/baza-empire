#!/usr/bin/env python3
"""List all photos for a project by section (before/during/after)."""
import os, json, glob

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
project_id = args.get("project_id", "")
base_dir = args.get("base_dir", "/home/switchhacker/baza-empire/agent-framework-v3/dashboard/artifacts")

photos = {"before": [], "during": [], "after": [], "other": []}
search_dir = os.path.join(base_dir, project_id) if project_id else base_dir

if os.path.isdir(search_dir):
    for ext in ["*.jpg", "*.jpeg", "*.png", "*.webp", "*.heic"]:
        for f in glob.glob(os.path.join(search_dir, "**", ext), recursive=True):
            rel = os.path.relpath(f, search_dir).lower()
            info = {"path": f, "name": os.path.basename(f), "size_kb": round(os.path.getsize(f) / 1024, 1)}
            if "before" in rel:
                photos["before"].append(info)
            elif "during" in rel or "progress" in rel:
                photos["during"].append(info)
            elif "after" in rel or "complete" in rel:
                photos["after"].append(info)
            else:
                photos["other"].append(info)

total = sum(len(v) for v in photos.values())
print(json.dumps({"project_id": project_id, "total_photos": total, "photos": photos}))
