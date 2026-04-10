#!/usr/bin/env python3
"""Search for files by name/pattern."""
import os, json, glob

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
pattern = args.get("pattern", "*")
directory = args.get("dir", "/home/switchhacker/baza-empire/agent-framework-v3")
recursive = args.get("recursive", True)

try:
    if recursive:
        search = os.path.join(directory, "**", pattern)
        files = glob.glob(search, recursive=True)
    else:
        search = os.path.join(directory, pattern)
        files = glob.glob(search)
    # Exclude common noise
    files = [f for f in files if "__pycache__" not in f and ".git/" not in f]
    results = []
    for f in files[:100]:
        try:
            stat = os.stat(f)
            results.append({"path": f, "size": stat.st_size, "is_dir": os.path.isdir(f)})
        except OSError:
            results.append({"path": f})
    print(json.dumps({"pattern": pattern, "dir": directory, "matches": results, "count": len(files)}))
except Exception as e:
    print(json.dumps({"error": str(e)}))
