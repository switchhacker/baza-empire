#!/usr/bin/env python3
"""
Skill: create_skill
Specter dynamically creates a new Baza skill. Approval-gated.

By default, skills are created in the SHARED skills dir (skills/shared/)
on whichever node runs this — so all agents can use them.

Usage:
    SKILL_ARGS='{
        "name": "weather_alerts",
        "description": "Fetches weather alerts for a location",
        "code": "#!/usr/bin/env python3\\nimport os, json\\nargs = json.loads(os.environ.get(\\"SKILL_ARGS\\", \\"{}\\"))\\nprint(\\"hello\\")",
        "target": "phantom|baza|both",
        "agent": "optional — if set, creates in agents/<agent>/skills/"
    }'
"""
import os
import sys
import json
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _specter_approval import request_approval, log_creation

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
name = args.get("name", "").strip()
description = args.get("description", "").strip()
code = args.get("code", "")
target = args.get("target", "phantom")
agent_specific = args.get("agent", "").strip()

if not name or not code:
    print("Error: 'name' and 'code' are required")
    sys.exit(1)

# Validate name
if not name.replace("_", "").isalnum():
    print(f"Error: skill name '{name}' must be alphanumeric + underscores only")
    sys.exit(1)

# Default to Python
filename = name if name.endswith((".py", ".sh")) else name + ".py"

# Build destination path
FRAMEWORK = os.environ.get("BAZA_FRAMEWORK_DIR", "/home/switchhacker/baza-empire/agent-framework-v3")
if agent_specific:
    dest_dir = os.path.join(FRAMEWORK, "agents", agent_specific, "skills")
else:
    dest_dir = os.path.join(FRAMEWORK, "skills", "shared")

dest_path = os.path.join(dest_dir, filename)

# Approval request
preview = code if len(code) < 1400 else code[:1400] + "\n...(truncated)"
details = f"File: {dest_path}\nTarget node(s): {target}\nDescription: {description}\nSize: {len(code)} chars"

approved = request_approval(
    category="skill",
    title=f"Create skill: {name}",
    details=details,
    preview=preview,
    timeout=300,
)

if not approved:
    log_creation("specter_voss", "skill", name, False, {"path": dest_path})
    print("DENIED or TIMEOUT — skill not created")
    sys.exit(0)

# Create on phantom (local)
if target in ("phantom", "both", "local"):
    try:
        os.makedirs(dest_dir, exist_ok=True)
        with open(dest_path, "w") as f:
            f.write(code)
        os.chmod(dest_path, 0o755)
        print(f"✓ Created on phantom: {dest_path}")
    except Exception as e:
        print(f"✗ phantom create failed: {e}")

# Create on baza via SSH
if target in ("baza", "both"):
    BAZA_HOST = os.environ.get("BAZA_MAIN_HOST", "100.127.118.103")
    BAZA_USER = os.environ.get("BAZA_MAIN_USER", "switchhacker")
    try:
        # Escape code for heredoc
        import tempfile
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False)
        tmp.write(code)
        tmp.close()
        scp = subprocess.run(
            ["scp", "-o", "StrictHostKeyChecking=no", tmp.name, f"{BAZA_USER}@{BAZA_HOST}:{dest_path}"],
            capture_output=True, text=True, timeout=30,
        )
        os.unlink(tmp.name)
        if scp.returncode == 0:
            subprocess.run(
                ["ssh", "-o", "StrictHostKeyChecking=no", f"{BAZA_USER}@{BAZA_HOST}", f"chmod +x {dest_path}"],
                capture_output=True, timeout=10,
            )
            print(f"✓ Created on baza: {dest_path}")
        else:
            print(f"✗ baza scp failed: {scp.stderr[:200]}")
    except Exception as e:
        print(f"✗ baza create failed: {e}")

log_creation("specter_voss", "skill", name, True, {"path": dest_path, "target": target})
print(f"\n✓ Skill '{name}' is live. Invoke via SKILL_ARGS='{{\"...\"}}'")
