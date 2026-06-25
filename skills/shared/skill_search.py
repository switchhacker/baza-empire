#!/usr/bin/env python3
"""Search the skill registry for skills/tools matching a query. Lets an agent
discover capabilities mid-task instead of relying only on the injected list."""
SKILL_META = {
    "category": "general",
    "summary": "Search the skill/tool registry by keyword.",
    "when_to_use": "When you need a capability not in the listed skills.",
    "args": {"query": "keywords describing the capability", "top_k": "int, optional"},
}
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from core import skill_registry as reg

try:
    args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
except json.JSONDecodeError as e:
    print(f"skill_search: invalid SKILL_ARGS JSON: {e}")
    sys.exit(1)
query = args.get("query", "")
try:
    top_k = int(args.get("top_k", 8))
except (TypeError, ValueError):
    top_k = 8
db_path = os.environ.get("SKILL_MANIFEST_DB", reg.DEFAULT_DB)

hits = reg.search(query, db_path=db_path, top_k=top_k)
if not hits:
    print(f"No skills found for query: {query!r}")
else:
    for h in hits:
        kind = h.get("type", "skill")
        print(f"- {h['name']} [{kind}/{h.get('category','')}] — {h.get('summary','')}")
