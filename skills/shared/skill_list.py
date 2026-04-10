#!/usr/bin/env python3
"""List all available agent skills (shared + per-agent)."""
import os, json, glob

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../..")

shared = sorted([os.path.basename(f).replace(".py","") for f in glob.glob(os.path.join(base, "skills/shared/*.py")) if not f.endswith("__init__.py")])

agent_skills = {}
for agent_dir in glob.glob(os.path.join(base, "agents/*/skills")):
    agent_name = os.path.basename(os.path.dirname(agent_dir))
    skills = sorted([os.path.basename(f).replace(".py","") for f in glob.glob(os.path.join(agent_dir, "*.py")) if not f.endswith("__init__.py")])
    if skills:
        agent_skills[agent_name] = skills

category = args.get("category", "")
if category:
    shared = [s for s in shared if category.lower() in s.lower()]

print(json.dumps({"shared_count": len(shared), "shared": shared, "agent_skills": agent_skills, "total": len(shared) + sum(len(v) for v in agent_skills.values())}))
