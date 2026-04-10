#!/usr/bin/env python3
"""Create a new file with content."""
import os, json

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
filepath = args.get("path", "")
content = args.get("content", "")
overwrite = args.get("overwrite", False)

BASE = "/home/switchhacker/baza-empire/agent-framework-v3"

if not filepath:
    print(json.dumps({"error": "No file path provided"}))
elif not filepath.startswith(BASE) and not filepath.startswith("/tmp"):
    print(json.dumps({"error": "Path must be within project directory or /tmp"}))
elif os.path.exists(filepath) and not overwrite:
    print(json.dumps({"error": "File already exists. Set overwrite=true to replace."}))
else:
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w") as f:
            f.write(content)
        print(json.dumps({"created": filepath, "size": len(content)}))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
