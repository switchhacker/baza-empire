#!/usr/bin/env python3
"""Calculate MD5/SHA256 hash of a file."""
import os, json, hashlib
args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
path = args.get("path", "")
algo = args.get("algorithm", "sha256")
if not path or not os.path.exists(path): print(json.dumps({"error": "File not found"}))
else:
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""): h.update(chunk)
    print(json.dumps({"hash": h.hexdigest(), "algorithm": algo, "file": path, "size": os.path.getsize(path)}))
