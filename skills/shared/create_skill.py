#!/usr/bin/env python3
"""
Baza Empire Meta-Skill — create_skill
Allows agents to dynamically create new skill scripts at runtime.

Usage:
  ##SKILL:create_skill{"name":"tool_name","description":"what it does","code":"#!/usr/bin/env python3\nimport os,json\nargs=json.loads(os.environ.get('SKILL_ARGS','{}'))\nprint('hello')"}##

The code must:
  - Read args from: args = json.loads(os.environ.get('SKILL_ARGS', '{}'))
  - Print result to stdout (that becomes the skill output)
  - Exit 0 on success, non-zero on failure
"""
import os, json, stat, sys, re

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
agent_id = os.environ.get("AGENT_ID", "unknown")

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SKILLS_SHARED = os.path.join(FRAMEWORK_DIR, "skills", "shared")

name        = args.get("name", "").strip()
description = args.get("description", "").strip()
code        = args.get("code", "").strip()

# ── Validation ────────────────────────────────────────────────────────────────

if not name:
    print("ERROR: 'name' is required", file=sys.stderr)
    sys.exit(1)

if not re.match(r'^[a-z][a-z0-9_]{1,49}$', name):
    print(f"ERROR: name '{name}' must be snake_case, 2-50 chars, letters/digits/underscore only", file=sys.stderr)
    sys.exit(1)

if not code:
    print("ERROR: 'code' is required", file=sys.stderr)
    sys.exit(1)

# Prevent overwriting core meta-skills
PROTECTED = {"create_skill", "save_artifact", "artifact_save", "update_task"}
if name in PROTECTED:
    print(f"ERROR: '{name}' is a protected skill and cannot be overwritten", file=sys.stderr)
    sys.exit(1)

# ── Write the skill file ───────────────────────────────────────────────────────

skill_path = os.path.join(SKILLS_SHARED, f"{name}.py")
existed = os.path.exists(skill_path)

# Ensure the code has a proper shebang
if not code.startswith("#!"):
    code = "#!/usr/bin/env python3\n" + code

# Inject the standard args header if not present
if "SKILL_ARGS" not in code:
    inject = '\nimport os as _os, json as _json\n_args = _json.loads(_os.environ.get("SKILL_ARGS", "{}"))\n'
    # Insert after the shebang + any existing imports at the top
    lines = code.split("\n")
    insert_at = 1
    for i, line in enumerate(lines[1:], 1):
        if line.startswith("#") or line.strip() == "" or line.startswith("import") or line.startswith("from"):
            insert_at = i + 1
        else:
            break
    lines.insert(insert_at, inject)
    code = "\n".join(lines)

try:
    with open(skill_path, "w") as f:
        f.write(code)
    # Make executable
    os.chmod(skill_path, os.stat(skill_path).st_mode | stat.S_IXUSR | stat.S_IXGRP)
except Exception as e:
    print(f"ERROR: Failed to write skill file: {e}", file=sys.stderr)
    sys.exit(1)

# ── Register in PostgreSQL (best-effort — don't fail if DB unavailable) ───────
try:
    sys.path.insert(0, FRAMEWORK_DIR)
    from core.context_db import register_skill
    register_skill(
        agent_id=agent_id,
        skill_name=name,
        description=description or f"Custom skill created by {agent_id}",
        script_path=skill_path,
        parameters={}
    )
    db_registered = True
except Exception as e:
    db_registered = False

# ── Report ────────────────────────────────────────────────────────────────────
action = "Updated" if existed else "Created"
lines = [
    f"✅ Skill {action.lower()}: {name}",
    f"📄 Path: {skill_path}",
    f"📝 Description: {description or '(none)'}",
    f"🗄️  DB registered: {'yes' if db_registered else 'no (DB unavailable — skill still works)'}",
    f"",
    f"Call it now with:",
    f'  ##SKILL:{name}{{}}##',
]
print("\n".join(lines))
