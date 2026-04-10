#!/usr/bin/env python3
"""
Baza Empire — List Artifacts Skill
Returns a list of recent artifacts from the dashboard/artifacts/ directory.
Agents call this to see what other agents have produced.

SKILL_ARGS:
  project_id  — filter by project (e.g. "proj-ahb123") — optional
  agent_id    — filter by agent name prefix in filename (e.g. "sam_axe") — optional
  limit       — max results (default 20)

Output: JSON list of {name, project_id, size, modified, ext, agent}
"""
import os
import sys
import json
import time

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ARTIFACTS_DIR = os.path.join(FRAMEWORK_DIR, "dashboard", "artifacts")

args         = json.loads(os.environ.get('SKILL_ARGS', '{}'))
proj_filter  = args.get('project_id', '').strip()
agent_filter = args.get('agent_id', '').strip()
limit        = int(args.get('limit', 20))

if not os.path.exists(ARTIFACTS_DIR):
    print(json.dumps([]))
    sys.exit(0)

files = []
for proj in os.listdir(ARTIFACTS_DIR):
    proj_path = os.path.join(ARTIFACTS_DIR, proj)
    if not os.path.isdir(proj_path):
        continue
    if proj_filter and proj != proj_filter:
        continue
    for fname in os.listdir(proj_path):
        fpath = os.path.join(proj_path, fname)
        if not os.path.isfile(fpath):
            continue
        # Infer agent from filename prefix (e.g. "sam_axe_20260401_brief.md" → "sam_axe")
        parts = fname.split('_')
        agent = '_'.join(parts[:2]) if len(parts) >= 2 else parts[0]
        if agent_filter and agent_filter not in fname:
            continue
        stat = os.stat(fpath)
        files.append({
            "name":       fname,
            "project_id": proj,
            "agent":      agent,
            "size":       stat.st_size,
            "size_human": f"{stat.st_size // 1024}KB" if stat.st_size >= 1024 else f"{stat.st_size}B",
            "modified":   time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
            "ext":        os.path.splitext(fname)[1].lower(),
            "mtime":      stat.st_mtime,
        })

# Sort by most recently modified
files.sort(key=lambda x: x['mtime'], reverse=True)
for f in files:
    del f['mtime']  # remove sort key before output

results = files[:limit]

# Print compact summary for LLM context
lines = [f"Found {len(results)} artifacts:"]
for f in results:
    lines.append(f"  [{f['project_id']}] {f['name']} ({f['size_human']}, {f['modified']}, agent: {f['agent']})")

print("\n".join(lines))
print()
print(json.dumps(results, indent=2))
