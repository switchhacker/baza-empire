#!/usr/bin/env python3
"""
Skill: list_tools
Returns a complete inventory of every tool Specter has available —
CLI agents, Ollama models, Baza skills, OpenClaw plugins, MCP servers.

Usage:
    SKILL_ARGS='{}'              # full inventory
    SKILL_ARGS='{"category":"cli"}'       # just CLI agents
    SKILL_ARGS='{"category":"skills"}'    # just Baza skills
    SKILL_ARGS='{"category":"models"}'    # just cloud models
    SKILL_ARGS='{"category":"plugins"}'   # just OpenClaw plugins
"""
import os
import sys
import json
import subprocess

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
category = args.get("category", "all")

def show_cli():
    print("🧠 CLI CODING AGENTS")
    tools = [
        ("claude", "/usr/bin/claude", "Anthropic Claude Code"),
        ("gemini", "/usr/bin/gemini", "Google Gemini CLI (60 req/min free)"),
        ("pi", "/usr/bin/pi", "Ollama Pi — coding agent"),
        ("opencode", "/usr/bin/opencode", "OpenCode — terminal coding assistant"),
        ("openclaw", "/usr/bin/openclaw", "OpenClaw — your own brain"),
        ("claw", "/usr/local/bin/claw", "Claw CLI 2.0 — Baza custom dev agent"),
        ("ollama", "/usr/local/bin/ollama", "Ollama runtime + cloud access"),
    ]
    for name, path, desc in tools:
        installed = "✅" if os.path.exists(path) else "❌"
        print(f"  {installed} {name:10} — {desc}")


def show_models():
    print("\n☁️  OLLAMA CLOUD MODELS")
    try:
        proc = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=10)
        cloud_models = [l for l in proc.stdout.split("\n") if ":cloud" in l]
        if cloud_models:
            for m in cloud_models:
                parts = m.split()
                if parts:
                    print(f"  ☁  {parts[0]}")
        else:
            print("  (no cloud models pulled — run: ollama pull qwen3.5:cloud)")
    except Exception as e:
        print(f"  (ollama not reachable: {e})")

    print("\n  Available cloud model families:")
    for name, desc in [
        ("qwen3.5:cloud (397B)", "fast general-purpose"),
        ("glm-5:cloud (744B)", "best agentic + coding"),
        ("kimi-k2.5:cloud", "multimodal + native agentic"),
        ("gemma4:31b-cloud", "256K context window"),
        ("gpt-oss:120b-cloud", "built-in web browsing"),
        ("qwen3-coder:480b-cloud", "heavy code generation"),
    ]:
        print(f"    • {name:28} — {desc}")


def show_skills():
    print("\n🛡  BAZA SKILLS (via baza_bridge.py)")
    framework = os.environ.get("BAZA_FRAMEWORK_DIR", "/home/switchhacker/baza-empire/agent-framework-v3")
    specter_skills = os.path.join(framework, "agents/specter_voss/skills")
    shared_skills = os.path.join(framework, "skills/shared")

    print("  Specter native:")
    if os.path.isdir(specter_skills):
        for f in sorted(os.listdir(specter_skills)):
            if f.endswith(".py") and not f.startswith("_"):
                name = f[:-3]
                # Extract docstring first line
                desc = ""
                try:
                    with open(os.path.join(specter_skills, f)) as fh:
                        content = fh.read(2000)
                        if '"""' in content:
                            doc = content.split('"""')[1].strip()
                            # Get second line (first is usually "Skill: name")
                            lines = doc.split("\n")
                            if len(lines) > 1:
                                desc = lines[1].strip()
                except Exception:
                    pass
                print(f"    • {name:22} — {desc[:60]}")

    print(f"\n  Shared Baza skills ({shared_skills}):")
    if os.path.isdir(shared_skills):
        shared = sorted(f[:-3] for f in os.listdir(shared_skills) if f.endswith(".py") and not f.startswith("_"))
        # Print in 3 columns
        for i in range(0, len(shared), 3):
            row = shared[i:i+3]
            print("    " + "  ".join(f"• {name:24}" for name in row))


def show_plugins():
    print("\n🦞 OPENCLAW PLUGINS (loaded)")
    try:
        proc = subprocess.run(["openclaw", "plugins", "list"], capture_output=True, text=True, timeout=15)
        lines = proc.stdout.split("\n")
        loaded = []
        for line in lines:
            if "│ loaded" in line or "│ loaded   │" in line:
                parts = [p.strip() for p in line.split("│") if p.strip()]
                if len(parts) >= 2:
                    plugin_id = parts[1] if len(parts) > 1 else "?"
                    loaded.append(plugin_id)
        # Print in 4 columns
        loaded = sorted(set(loaded))
        print(f"  ({len(loaded)} plugins loaded)")
        for i in range(0, len(loaded), 4):
            row = loaded[i:i+4]
            print("    " + "  ".join(f"{n:16.16}" for n in row))
    except Exception as e:
        print(f"  (openclaw not reachable: {e})")


def show_data():
    print("\n📊 DATA & INFRASTRUCTURE ACCESS")
    print("  • baza PostgreSQL  — 100.127.118.103:5432 (baza_agents db)")
    print("    - messages, task_journal, empire_knowledge, agent_memory")
    print("    - team_activity view (all 9 agents unified)")
    print("  • baza Redis       — 100.127.118.103:6379 (events + heartbeats)")
    print("  • baza Dashboard   — http://100.127.118.103:8888")
    print("  • baza SSH         — switchhacker@100.127.118.103 (key auth)")
    print("  • Tailscale mesh   — encrypted, always-on")
    print("  • Data Hub         — http://100.127.118.103:8888/datahub")


if category in ("all", "cli"): show_cli()
if category in ("all", "models"): show_models()
if category in ("all", "skills"): show_skills()
if category in ("all", "plugins"): show_plugins()
if category in ("all", "data", "infra"): show_data()

if category == "all":
    print("\n💡 Read full docs: ~/.openclaw/workspace/TOOLS.md")
