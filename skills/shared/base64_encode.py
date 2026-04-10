#!/usr/bin/env python3
"""Encode/decode base64."""
import os, json, base64
args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
mode = args.get("mode", "encode")
text = args.get("text", "")
if mode == "encode": print(json.dumps({"result": base64.b64encode(text.encode()).decode()}))
else:
    try: print(json.dumps({"result": base64.b64decode(text).decode()}))
    except Exception as e: print(json.dumps({"error": str(e)}))
