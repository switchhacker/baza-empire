#!/usr/bin/env python3
"""
Baza Bridge — Connects OpenClaw to the Baza Empire skill engine.
Runs Baza skills as subprocesses and returns results to OpenClaw.

This is the main tool that OpenClaw calls to execute any Baza skill.
It wraps the SkillsEngine pattern so OpenClaw can invoke:
  - baza_scan, agent_pulse, code_scan, log_scan
  - knowledge_dump, publish_insight
  - stealth_deploy, stealth_skill, stealth_restart, stealth_install
  - Any shared skill (weather, crypto, news, web_search, etc.)

Usage from OpenClaw:
  /run baza_scan
  /run agent_pulse {"agent":"claw_batto"}
  /run stealth_deploy {"branch":"main"}
"""
import os
import sys
import json
import subprocess

# Framework paths
FRAMEWORK_DIR = os.environ.get(
    "BAZA_FRAMEWORK_DIR",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
)
AGENT_SKILLS_DIR = os.path.join(FRAMEWORK_DIR, "agents", "specter_voss", "skills")
SHARED_SKILLS_DIR = os.path.join(FRAMEWORK_DIR, "skills", "shared")
VENV_PYTHON = os.path.join(FRAMEWORK_DIR, "venv", "bin", "python")
if not os.path.exists(VENV_PYTHON):
    VENV_PYTHON = "python3"


def find_skill(name):
    """Find a skill script by name (agent-specific first, then shared)."""
    for base in [AGENT_SKILLS_DIR, SHARED_SKILLS_DIR]:
        for ext in [".py", ".sh"]:
            path = os.path.join(base, name + ext)
            if os.path.exists(path):
                return path
    return None


def run_skill(name, args=None):
    """Execute a Baza skill and return the output."""
    path = find_skill(name)
    if not path:
        available = []
        for d in [AGENT_SKILLS_DIR, SHARED_SKILLS_DIR]:
            if os.path.isdir(d):
                available.extend(
                    f.replace(".py", "").replace(".sh", "")
                    for f in os.listdir(d)
                    if f.endswith((".py", ".sh")) and not f.startswith("_")
                )
        return {
            "success": False,
            "error": f"Skill '{name}' not found",
            "available": sorted(set(available))
        }

    env = os.environ.copy()
    env["SKILL_ARGS"] = json.dumps(args or {})
    env["AGENT_ID"] = "specter_voss"

    cmd = [VENV_PYTHON, path] if path.endswith(".py") else ["bash", path]

    # Longer timeout for stealth operations
    timeout = 300 if "stealth" in name else 90

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, env=env, cwd=FRAMEWORK_DIR
        )
        return {
            "success": proc.returncode == 0,
            "output": proc.stdout.strip() if proc.returncode == 0 else proc.stderr.strip(),
            "skill": name
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Skill '{name}' timed out ({timeout}s)"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def list_skills():
    """List all available skills."""
    skills = []
    for label, d in [("specter", AGENT_SKILLS_DIR), ("shared", SHARED_SKILLS_DIR)]:
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if f.endswith(".py") and not f.startswith("_"):
                name = f.replace(".py", "")
                skills.append({"name": name, "source": label, "path": os.path.join(d, f)})
    return skills


# ── CLI entry point ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] in ("--help", "-h", "help"):
        print("Baza Bridge — Run Baza skills from OpenClaw")
        print()
        print("Usage:")
        print("  python baza_bridge.py <skill_name> [json_args]")
        print("  python baza_bridge.py list")
        print()
        print("Examples:")
        print("  python baza_bridge.py baza_scan")
        print('  python baza_bridge.py agent_pulse \'{"agent":"claw_batto"}\'')
        print('  python baza_bridge.py stealth_deploy \'{"branch":"main"}\'')
        print()
        skills = list_skills()
        print(f"Available skills ({len(skills)}):")
        for s in skills:
            print(f"  [{s['source']}] {s['name']}")
        sys.exit(0)

    if sys.argv[1] == "list":
        for s in list_skills():
            print(f"  [{s['source']:7s}] {s['name']}")
        sys.exit(0)

    skill_name = sys.argv[1]
    args = {}
    if len(sys.argv) > 2:
        try:
            args = json.loads(sys.argv[2])
        except json.JSONDecodeError:
            # Treat as key=value pairs
            for kv in sys.argv[2:]:
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    args[k] = v

    result = run_skill(skill_name, args)

    if result.get("success"):
        print(result.get("output", ""))
    else:
        print(f"ERROR: {result.get('error', 'unknown')}")
        if result.get("available"):
            print(f"\nAvailable skills: {', '.join(result['available'][:20])}")
        sys.exit(1)
