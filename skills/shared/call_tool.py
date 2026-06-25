#!/usr/bin/env python3
"""Bridge: invoke any tool-server endpoint through the ##SKILL## path.
Lets an agent reach the ~100 HTTP tools (Sam imaging, Claw devops, edge, etc.)
with the same mechanism it uses for skills."""
SKILL_META = {
    "category": "general",
    "summary": "Call a tool-server endpoint (agent/tool) with an input dict.",
    "when_to_use": "To run an HTTP tool such as sam_axe/generate-image or claw_batto/run-command.",
    "args": {"agent": "e.g. sam_axe", "tool": "e.g. generate-image", "input": "dict of inputs"},
}
import json
import os
import sys

try:
    args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
except json.JSONDecodeError as e:
    print(json.dumps({"success": False, "error": f"invalid SKILL_ARGS JSON: {e}"}))
    sys.exit(1)
agent = (args.get("agent") or "").strip()
tool = (args.get("tool") or "").strip()
tool_input = args.get("input") or {}

if not agent or not tool:
    print(json.dumps({"success": False, "error": "call_tool requires non-empty 'agent' and 'tool'"}))
    sys.exit(1)

base = os.environ.get("TOOL_SERVER_URL", "http://localhost:8000")
slug_map = {"simon_bately": "simon", "claw_batto": "claw", "phil_hass": "phil", "sam_axe": "sam"}
slug = slug_map.get(agent, agent)
url = f"{base}/tools/{slug}/{tool}"

try:
    import httpx
    resp = httpx.post(url, json={"input": tool_input}, timeout=120)
    resp.raise_for_status()
    print(json.dumps(resp.json()))
except Exception as e:
    print(json.dumps({"success": False, "error": f"{type(e).__name__}: {e}", "tool": f"{slug}/{tool}"}))
