#!/usr/bin/env python3
"""Test a regex pattern against text."""
import os, json, re
args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
pattern = args.get("pattern", ""); text = args.get("text", "")
try:
    matches = re.findall(pattern, text)
    print(json.dumps({"matches": matches[:20], "count": len(matches), "pattern": pattern}))
except re.error as e: print(json.dumps({"error": str(e)}))
