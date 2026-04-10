#!/usr/bin/env python3
"""List all agents with their specialties, titles, and what they can help with."""
import os, json, yaml

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../config/agents.yaml")

try:
    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    agents = []
    for aid, info in cfg.get("agents", {}).items():
        agents.append({
            "id": aid,
            "name": info.get("name", aid),
            "company_title": info.get("company_title", ""),
            "role": info.get("role", ""),
            "model": info.get("model", ""),
        })

    # Filter by specialty if requested
    query = args.get("query", "").lower()
    if query:
        agents = [a for a in agents if query in a["role"].lower() or query in a["company_title"].lower()]

    print(json.dumps({"count": len(agents), "agents": agents}))
except Exception as e:
    print(json.dumps({"error": str(e)}))
