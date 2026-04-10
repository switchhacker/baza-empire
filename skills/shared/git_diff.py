#!/usr/bin/env python3
"""Show git diff summary."""
import os, json, subprocess

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
repo = args.get("repo", "/home/switchhacker/baza-empire/agent-framework-v3")
staged = args.get("staged", False)

try:
    cmd = ["git", "diff", "--stat"]
    if staged:
        cmd.insert(2, "--cached")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10, cwd=repo)
    # Also get full diff (truncated)
    cmd2 = ["git", "diff"]
    if staged:
        cmd2.insert(2, "--cached")
    result2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=10, cwd=repo)
    diff_text = result2.stdout[:5000]
    print(json.dumps({
        "repo": repo, "staged": staged,
        "stat": result.stdout.strip(),
        "diff_preview": diff_text,
        "truncated": len(result2.stdout) > 5000
    }))
except Exception as e:
    print(json.dumps({"error": str(e)}))
