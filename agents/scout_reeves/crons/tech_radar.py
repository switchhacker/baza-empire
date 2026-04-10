#!/usr/bin/env python3
"""Scout Reeves — Weekly tech landscape: tools, AI, infra improvements for Baza."""
import os, sys, logging
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../.."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))
from agents.cron_helpers import *

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SCOUT-TECH] %(message)s")

MODEL = "claude-3-5-haiku"
AGENT_TOKEN = os.getenv("TELEGRAM_SCOUT_REEVES", TELEGRAM_TOKEN)

def collect_data():
    # Current tech stack
    ollama_models = run_cmd("curl -s http://localhost:11434/api/tags 2>/dev/null | python3 -c \"import sys,json; [print('  '+m['name']) for m in json.load(sys.stdin).get('models',[])]\" 2>/dev/null")
    disk = run_cmd("df -h / --output=avail | tail -1")
    gpu = run_cmd("nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null")

    return f"""BAZA TECH STACK — {today()}

SERVER: AMD Ryzen 7 5700G, 62GB RAM, Ubuntu 24.04
GPUs: NVIDIA RTX 3070 (8GB), AMD RX 6700 XT (12GB)
STORAGE: 86TB total, {disk} free on root
NETWORK: Tailscale VPN

LOCAL LLM MODELS:
{ollama_models or '  (unable to query)'}

CLOUD MODELS: GPT-4o, Claude 3.5, Gemini via LiteLLM proxy
SERVICES: PostgreSQL, Redis, Flask dashboard, Ollama x2, LiteLLM

FRAMEWORK: 8 AI agents, 145+ skills, event bus, task runner
DASHBOARD: 200+ API routes, mobile PWA, Baza Cloud

AREAS OF INTEREST:
- Better local LLM models for agents
- GPU optimization (dual GPU utilization)
- Storage management for 86TB
- Automation and CI/CD
- Security hardening
- AI agent coordination improvements
"""

def main():
    log.info("Starting tech radar...")
    data = collect_data()
    system = f"""You are Scout Reeves — Director of Research & Market Intelligence.
Weekly tech radar for Baza Empire. Plain text, no markdown. Max 30 lines.

Based on your knowledge through early 2025:
- New LLM models worth trying locally (smaller, faster, better at tasks)
- Infrastructure tools that would benefit the stack
- AI agent coordination patterns
- Security best practices for self-hosted setups
- Interesting open-source projects for construction business automation

Be specific with recommendations. Include model names, tool names, links if known.

{data}"""

    try:
        import urllib.request
        payload = json.dumps({
            "model": MODEL, "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": f"Weekly tech radar for {today()}"}
            ], "max_tokens": 1000, "temperature": 0.7
        }).encode()
        litellm_key = os.getenv("LITELLM_MASTER_KEY", "baza-litellm-internal")
        req = urllib.request.Request("http://localhost:4000/v1/chat/completions",
                                     data=payload, headers={"Content-Type": "application/json",
                                                            "Authorization": f"Bearer {litellm_key}"})
        with urllib.request.urlopen(req, timeout=90) as r:
            report = json.loads(r.read())["choices"][0]["message"]["content"]
    except Exception:
        report = ollama_generate("qwen2.5:14b", system, f"Weekly tech radar for {today()}")

    save_artifact("proj-research", f"tech_radar_{today()}.md", f"# Tech Radar — {today()}\n\n{report}")
    publish_event("scout_reeves", "research_complete", {"topic": "weekly_tech_radar", "summary": report[:200]})
    send_telegram(f"📡 TECH RADAR — {today()}\n\n{report}\n\n💬 Was this sufficient? Reply with feedback or 'ok'.", AGENT_TOKEN)

if __name__ == "__main__":
    main()
