#!/usr/bin/env python3
"""
Skill: cloud_query
Direct query to any Ollama cloud model — bypasses OpenClaw for raw model access.
Useful when Specter wants to delegate a specific task to a specialized cloud model.

Usage:
    SKILL_ARGS='{"model":"qwen3-coder:480b-cloud","prompt":"write a python web scraper"}'
    SKILL_ARGS='{"model":"glm-5:cloud","prompt":"analyze this for security issues","temperature":0.3}'
    SKILL_ARGS='{"model":"kimi-k2.5:cloud","prompt":"research the latest AI trends"}'
    SKILL_ARGS='{"model":"gpt-oss:120b-cloud","prompt":"browse https://example.com and summarize"}'
"""
import os
import json
import sys
import socket
import urllib.request

# Force IPv6 preference
_orig = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _orig(*a, **k) if r[0] == socket.AF_INET6] or _orig(*a, **k)

CLOUD_MODELS = {
    "qwen3.5:cloud": "397B general — fast default",
    "glm-5:cloud": "744B MoE — best for agentic + coding",
    "kimi-k2.5:cloud": "Multimodal, agentic with subagents",
    "gemma4:31b-cloud": "31B, 256K context",
    "gpt-oss:120b-cloud": "120B with built-in web browsing",
    "qwen3-coder:480b-cloud": "480B coding specialist",
    "deepseek-v3.1:671-cloud": "671B reasoning",
}

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
model = args.get("model", "qwen3.5:cloud")
prompt = args.get("prompt", "")
system = args.get("system", "")
temperature = args.get("temperature", 0.7)
max_tokens = args.get("max_tokens", 2000)

if not prompt:
    print("Error: 'prompt' required")
    print("\nAvailable models:")
    for m, desc in CLOUD_MODELS.items():
        print(f"  {m}  — {desc}")
    sys.exit(1)

# Use the ollama CLI which handles cloud auth automatically
import subprocess

full_prompt = prompt
if system:
    full_prompt = f"[SYSTEM]\n{system}\n\n[USER]\n{prompt}"

env = os.environ.copy()
env.setdefault("GODEBUG", "netdns=go+4")

try:
    proc = subprocess.run(
        ["/usr/local/bin/ollama", "run", model, full_prompt],
        capture_output=True,
        text=True,
        timeout=240,
        env=env,
    )
    if proc.returncode == 0:
        # Strip ANSI codes
        import re
        out = re.sub(r'\x1b\[[0-9;?]*[a-zA-Z]', '', proc.stdout).strip()
        # Remove "...done thinking." artifacts
        out = re.sub(r'\.\.\.done thinking\.\s*', '', out).strip()
        print(out)
        print(f"\n[{model}]", file=sys.stderr)
    else:
        print(f"Error [{proc.returncode}]: {proc.stderr[:500]}")
        sys.exit(1)
except subprocess.TimeoutExpired:
    print(f"Cloud query timed out after 240s")
    sys.exit(1)
except FileNotFoundError:
    # Try /usr/bin/ollama
    try:
        proc = subprocess.run(
            ["/usr/bin/ollama", "run", model, full_prompt],
            capture_output=True, text=True, timeout=240, env=env,
        )
        print(proc.stdout if proc.returncode == 0 else proc.stderr)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)
