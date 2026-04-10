#!/usr/bin/env python3
"""Pretty-print/validate JSON."""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
text = args.get("text", args.get("json", ""))
try:
    parsed = json.loads(text) if isinstance(text, str) else text
    print(json.dumps({"valid": True, "formatted": json.dumps(parsed, indent=2, default=str)[:5000]}))
except json.JSONDecodeError as e: print(json.dumps({"valid": False, "error": str(e)}))
