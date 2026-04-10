#!/usr/bin/env python3
"""Format Python code with black."""
import os, json, subprocess

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
filepath = args.get("file", "")
check_only = args.get("check", False)

if not filepath:
    print(json.dumps({"error": "No file provided"}))
else:
    try:
        cmd = ["python", "-m", "black", "--line-length=120"]
        if check_only:
            cmd.append("--check")
        cmd.append(filepath)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        print(json.dumps({
            "file": filepath, "check_only": check_only,
            "formatted": result.returncode == 0,
            "output": result.stderr.strip() or result.stdout.strip()
        }))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
