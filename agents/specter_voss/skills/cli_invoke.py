#!/usr/bin/env python3
"""
Skill: cli_invoke
Specter can invoke any installed CLI tool: pi, opencode, claude, gemini, ollama, openclaw.
Returns the tool's output.

Usage:
    SKILL_ARGS='{"tool":"claude","args":"--print fix this bug","stdin":"def add(a,b):\\n  return a-b"}'
    SKILL_ARGS='{"tool":"opencode","args":"refactor this file","files":["app.py"]}'
    SKILL_ARGS='{"tool":"gemini","args":"--prompt explain this code","stdin":"..."}'
    SKILL_ARGS='{"tool":"pi","args":"@app.py write tests"}'
    SKILL_ARGS='{"tool":"ollama","args":"run qwen3-coder:480b-cloud write a python web scraper"}'
"""
import os
import json
import sys
import subprocess
import shlex

ALLOWED_TOOLS = {
    "claude": "/usr/bin/claude",
    "gemini": "/usr/bin/gemini",
    "pi": "/usr/bin/pi",
    "opencode": "/usr/bin/opencode",
    "openclaw": "/usr/bin/openclaw",
    "ollama": "/usr/bin/ollama",
    "claw": "/usr/local/bin/claw",
}

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
tool = args.get("tool", "")
arg_str = args.get("args", "")
stdin_data = args.get("stdin", "")
timeout = int(args.get("timeout", 180))

if not tool or tool not in ALLOWED_TOOLS:
    print(f"Error: 'tool' must be one of: {', '.join(ALLOWED_TOOLS.keys())}")
    sys.exit(1)

binary = ALLOWED_TOOLS[tool]
if not os.path.exists(binary):
    print(f"Error: {tool} not installed at {binary}")
    sys.exit(1)

# Build command
try:
    cmd_args = shlex.split(arg_str) if arg_str else []
except Exception as e:
    print(f"Error parsing args: {e}")
    sys.exit(1)

cmd = [binary] + cmd_args

# Inherit environment with API keys
env = os.environ.copy()
env.setdefault("GODEBUG", "netdns=go+4")

try:
    proc = subprocess.run(
        cmd,
        input=stdin_data,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    if proc.returncode == 0:
        print(proc.stdout)
    else:
        print(f"[exit {proc.returncode}]")
        if proc.stdout:
            print(proc.stdout)
        if proc.stderr:
            print(f"STDERR: {proc.stderr[:500]}")
except subprocess.TimeoutExpired:
    print(f"Tool '{tool}' timed out after {timeout}s")
except Exception as e:
    print(f"Error: {e}")
