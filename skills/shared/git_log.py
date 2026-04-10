#!/usr/bin/env python3
"""Get recent git commits."""
import os, json, subprocess

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
repo = args.get("repo", "/home/switchhacker/baza-empire/agent-framework-v3")
count = int(args.get("count", 10))

try:
    result = subprocess.run(
        ["git", "log", f"-{count}", "--format=%H|%h|%an|%ai|%s"],
        capture_output=True, text=True, timeout=10, cwd=repo)
    commits = []
    for line in result.stdout.strip().split("\n"):
        if "|" in line:
            parts = line.split("|", 4)
            commits.append({
                "hash": parts[0], "short": parts[1], "author": parts[2],
                "date": parts[3], "message": parts[4]
            })
    print(json.dumps({"repo": repo, "commits": commits}))
except Exception as e:
    print(json.dumps({"error": str(e)}))
