#!/usr/bin/env python3
"""List running Docker containers with status."""
import os, json, subprocess

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
all_containers = args.get("all", False)

try:
    cmd = ["docker", "ps", "--format", "{{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}"]
    if all_containers:
        cmd.insert(2, "-a")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    containers = []
    for line in result.stdout.strip().split("\n"):
        if line.strip():
            parts = line.split("\t")
            containers.append({
                "id": parts[0] if len(parts) > 0 else "",
                "name": parts[1] if len(parts) > 1 else "",
                "image": parts[2] if len(parts) > 2 else "",
                "status": parts[3] if len(parts) > 3 else "",
                "ports": parts[4] if len(parts) > 4 else ""
            })
    print(json.dumps({"containers": containers, "count": len(containers)}))
except Exception as e:
    print(json.dumps({"error": str(e)}))
