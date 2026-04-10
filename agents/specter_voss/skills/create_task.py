#!/usr/bin/env python3
"""
Skill: create_task
Specter creates a task in the Baza task queue (SQLite dashboard DB).
Approval-gated. Once created, the assigned agent picks it up via task_runner.

Usage:
    SKILL_ARGS='{
        "title": "Research competitor pricing in Bucks County",
        "description": "Pull 5 competitor websites, summarize pricing",
        "assigned_to": "scout_reeves",
        "priority": "high",
        "project_id": "proj-baza-empire",
        "due_date": "2026-04-15"
    }'
"""
import os
import sys
import json
import uuid
import subprocess
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _specter_approval import request_approval, log_creation

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
title = args.get("title", "").strip()
description = args.get("description", "").strip()
assigned_to = args.get("assigned_to", "").strip()
priority = args.get("priority", "medium").strip()
project_id = args.get("project_id", "proj-baza-empire").strip()
due_date = args.get("due_date", "").strip()

if not title or not assigned_to:
    print("Error: 'title' and 'assigned_to' are required")
    sys.exit(1)

VALID_AGENTS = {"simon_bately", "claw_batto", "phil_hass", "sam_axe",
                "rex_valor", "duke_harmon", "scout_reeves", "nova_sterling", "specter_voss"}
if assigned_to not in VALID_AGENTS:
    print(f"Error: assigned_to must be one of: {', '.join(sorted(VALID_AGENTS))}")
    sys.exit(1)

details = f"Assigned to: {assigned_to}\nPriority: {priority}\nProject: {project_id}\nDue: {due_date or 'none'}\n\n{description[:500]}"

approved = request_approval(
    category="task",
    title=f"Task for {assigned_to}: {title}",
    details=details,
    timeout=300,
)

if not approved:
    log_creation("specter_voss", "task", title, False, {"assignee": assigned_to})
    print("DENIED")
    sys.exit(0)

# Insert via SSH to baza (task DB is SQLite on baza)
task_id = f"task-{uuid.uuid4().hex[:12]}"
now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# Escape values safely
import shlex
sql = f"""INSERT INTO tasks (id, project_id, title, description, assigned_to, status, priority, due_date, created_at, updated_at)
VALUES ({shlex.quote(task_id)}, {shlex.quote(project_id)}, {shlex.quote(title)}, {shlex.quote(description)}, {shlex.quote(assigned_to)}, 'pending', {shlex.quote(priority)}, {shlex.quote(due_date)}, {shlex.quote(now)}, {shlex.quote(now)});"""

remote_cmd = f"sqlite3 /home/switchhacker/baza-empire/agent-framework-v3/dashboard/baza_projects.db \"{sql}\""

try:
    proc = subprocess.run(
        ["ssh", "-o", "StrictHostKeyChecking=no",
         f"{os.environ.get('BAZA_MAIN_USER','switchhacker')}@{os.environ.get('BAZA_MAIN_HOST','100.127.118.103')}",
         remote_cmd],
        capture_output=True, text=True, timeout=15,
    )
    if proc.returncode == 0:
        log_creation("specter_voss", "task", title, True,
                     {"task_id": task_id, "assignee": assigned_to, "priority": priority})
        print(f"✓ Task created: {task_id}")
        print(f"   {title}")
        print(f"   → assigned to {assigned_to} (priority: {priority})")
        print(f"   {assigned_to} will pick it up on the next task_runner cycle")
    else:
        print(f"✗ Insert failed: {proc.stderr[:200]}")
        sys.exit(1)
except Exception as e:
    print(f"✗ Error: {e}")
    sys.exit(1)
