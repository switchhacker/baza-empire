#!/usr/bin/env python3
"""Run a shell command and return output."""
import os, json, subprocess

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
command = args.get("command", "")
timeout = int(args.get("timeout", 30))
cwd = args.get("cwd", "/home/switchhacker/baza-empire/agent-framework-v3")

BLOCKED = ["rm -rf /", "mkfs", "dd if=", "> /dev/sd", "chmod -R 777 /"]

if not command:
    print(json.dumps({"error": "No command provided"}))
elif any(b in command for b in BLOCKED):
    print(json.dumps({"error": "Command blocked for safety"}))
else:
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True,
                              timeout=min(timeout, 120), cwd=cwd)
        print(json.dumps({
            "command": command, "returncode": result.returncode,
            "stdout": result.stdout[-5000:],
            "stderr": result.stderr[-2000:],
            "truncated": len(result.stdout) > 5000
        }))
    except subprocess.TimeoutExpired:
        print(json.dumps({"error": f"Command timed out after {timeout}s"}))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
