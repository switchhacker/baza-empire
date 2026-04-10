#!/usr/bin/env python3
"""Get git status of a repo."""
import os, json, subprocess

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
repo = args.get("repo", "/home/switchhacker/baza-empire/agent-framework-v3")

try:
    result = subprocess.run(["git", "status", "--porcelain", "-b"],
                          capture_output=True, text=True, timeout=10, cwd=repo)
    lines = result.stdout.strip().split("\n")
    branch = lines[0].replace("## ", "") if lines else "unknown"
    modified = [l[3:] for l in lines[1:] if l.startswith(" M") or l.startswith("M ")]
    added = [l[3:] for l in lines[1:] if l.startswith("A ") or l.startswith("??")]
    deleted = [l[3:] for l in lines[1:] if l.startswith(" D") or l.startswith("D ")]
    print(json.dumps({
        "repo": repo, "branch": branch,
        "modified": modified, "added": added, "deleted": deleted,
        "clean": len(lines) <= 1
    }))
except Exception as e:
    print(json.dumps({"error": str(e)}))
