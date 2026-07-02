#!/usr/bin/env python3
"""Save a fact to empire_knowledge so ALL agents can see it."""
import json
import os
import sys

SKILL_META = {
    "category": "knowledge",
    "summary": "Write or update a shared empire_knowledge fact (key + value + category) visible to every agent.",
    "when_to_use": "when you learn a durable fact worth sharing across the team — pricing, vendor info, project decisions, recurring client preferences",
    "args": {
        "key": "short snake_case key, e.g. 'preferred_drywall_vendor' (required)",
        "value": "the fact text (required)",
        "category": "grouping, default 'general'",
    },
}

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, BASE_DIR)

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
key = (args.get("key") or "").strip()
value = (args.get("value") or "").strip()
category = (args.get("category") or "general").strip()

if not key or not value:
    print(json.dumps({"error": "'key' and 'value' are required"}))
    sys.exit(1)
if len(value) > 4000:
    print(json.dumps({"error": "value too long (max 4000 chars) — summarize it"}))
    sys.exit(1)

try:
    from core.context_db import empire_set
    empire_set(key, value, category=category,
               updated_by=os.environ.get("AGENT_ID", "unknown"))
except Exception as e:
    print(json.dumps({"error": f"empire_knowledge write failed: {e}"}))
    sys.exit(1)

print(json.dumps({"ok": True, "key": key, "category": category,
                  "note": "Shared with all agents via empire_knowledge."}))
