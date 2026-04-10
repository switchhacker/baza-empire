#!/usr/bin/env python3
"""Read a file and return contents."""
import os, json

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
filepath = args.get("path", "")
lines = args.get("lines", 0)  # 0 = all
offset = int(args.get("offset", 0))

if not filepath:
    print(json.dumps({"error": "No file path provided"}))
elif not os.path.exists(filepath):
    print(json.dumps({"error": f"File not found: {filepath}"}))
else:
    try:
        with open(filepath, "r", errors="replace") as f:
            content = f.readlines()
        total = len(content)
        if offset:
            content = content[offset:]
        if lines:
            content = content[:int(lines)]
        text = "".join(content)
        print(json.dumps({
            "path": filepath, "total_lines": total,
            "content": text[:10000],
            "truncated": len(text) > 10000
        }))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
