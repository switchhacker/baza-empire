#!/usr/bin/env python3
"""Run flake8/pylint on a file."""
import os, json, subprocess

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
filepath = args.get("file", "")
linter = args.get("linter", "flake8")

if not filepath:
    print(json.dumps({"error": "No file provided"}))
else:
    try:
        if linter == "flake8":
            cmd = ["python", "-m", "flake8", "--max-line-length=120", filepath]
        else:
            cmd = ["python", "-m", "pylint", "--disable=C0114,C0115,C0116", filepath]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        issues = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
        print(json.dumps({
            "file": filepath, "linter": linter,
            "issues": issues, "count": len(issues),
            "clean": len(issues) == 0
        }))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
