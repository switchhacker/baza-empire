#!/usr/bin/env python3
"""Create a zip archive of files."""
import os, json, zipfile
args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
files = args.get("files", [])
output = args.get("output", "/tmp/archive.zip")
if not files: print(json.dumps({"error": "files list required"}))
else:
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            if os.path.exists(f): zf.write(f, os.path.basename(f))
    print(json.dumps({"file": output, "count": len(files), "size": os.path.getsize(output)}))
