#!/usr/bin/env python3
"""Search code for a pattern (grep wrapper)."""
import os, json, subprocess

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
pattern = args.get("pattern", "")
directory = args.get("dir", "/home/switchhacker/baza-empire/agent-framework-v3")
file_type = args.get("ext", "*.py")
case_sensitive = args.get("case_sensitive", False)

if not pattern:
    print(json.dumps({"error": "No search pattern provided"}))
else:
    try:
        cmd = ["grep", "-rn", "--include", file_type]
        if not case_sensitive:
            cmd.append("-i")
        cmd.extend([pattern, directory])
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        matches = []
        for line in result.stdout.strip().split("\n")[:50]:
            if ":" in line:
                parts = line.split(":", 2)
                matches.append({"file": parts[0], "line": parts[1], "text": parts[2].strip() if len(parts) > 2 else ""})
        print(json.dumps({"pattern": pattern, "matches": matches, "count": len(matches)}))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
