#!/usr/bin/env python3
"""Run pytest or unittest for a project."""
import os, json, subprocess

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
path = args.get("path", "/home/switchhacker/baza-empire/agent-framework-v3")
test_file = args.get("file", "")
framework = args.get("framework", "pytest")

try:
    if framework == "pytest":
        cmd = ["python", "-m", "pytest", "-v", "--tb=short"]
        if test_file:
            cmd.append(test_file)
    else:
        cmd = ["python", "-m", "unittest", "discover", "-v"]
        if test_file:
            cmd = ["python", "-m", "unittest", test_file, "-v"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=path)
    output = result.stdout + result.stderr
    print(json.dumps({
        "framework": framework, "returncode": result.returncode,
        "passed": result.returncode == 0,
        "output": output[-3000:]
    }))
except Exception as e:
    print(json.dumps({"error": str(e)}))
