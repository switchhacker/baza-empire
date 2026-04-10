#!/usr/bin/env python3
"""
Skill: create_cron
Specter adds a cron job to phantom or baza. Approval-gated.

Usage:
    SKILL_ARGS='{
        "name": "temp_check",
        "schedule": "*/15 * * * *",
        "command": "cd /home/switchhacker/baza-empire/agent-framework-v3 && ./venv/bin/python agents/specter_voss/skills/hw_temps.py",
        "target": "phantom|baza",
        "log": "/tmp/temp_check.log"
    }'
"""
import os
import sys
import json
import subprocess
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _specter_approval import request_approval, log_creation

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
name = args.get("name", "").strip()
schedule = args.get("schedule", "").strip()
command = args.get("command", "").strip()
target = args.get("target", "phantom").strip()
log_path = args.get("log", "").strip()

if not name or not schedule or not command:
    print("Error: 'name', 'schedule', and 'command' are required")
    sys.exit(1)

# Validate schedule (basic 5-field check)
sched_parts = schedule.split()
if len(sched_parts) != 5:
    print(f"Error: schedule must be 5 cron fields (got '{schedule}')")
    sys.exit(1)

# Build the cron line with tagging
if log_path:
    cron_cmd = f"{command} >> {log_path} 2>&1"
else:
    cron_cmd = command + " 2>&1"

tag = f"# specter-managed name={name}"
cron_line = f"{tag}\n{schedule} {cron_cmd}"

details = f"Name: {name}\nTarget: {target}\nSchedule: {schedule}\nCommand: {command[:300]}\nLog: {log_path or '(stdout discarded)'}"
preview = cron_line

approved = request_approval(
    category="cron",
    title=f"Create cron: {name} ({target})",
    details=details,
    preview=preview,
    timeout=300,
)

if not approved:
    log_creation("specter_voss", "cron", name, False, {"target": target})
    print("DENIED")
    sys.exit(0)


def install_cron_local(cron_line, name):
    """Install cron on local machine (phantom)."""
    # Fetch current crontab
    try:
        current = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    except Exception:
        current = ""

    # Remove any existing entry with same name
    lines = current.split("\n")
    cleaned = []
    skip_next = False
    for line in lines:
        if skip_next:
            skip_next = False
            continue
        if f"# specter-managed name={name}" in line:
            skip_next = True
            continue
        cleaned.append(line)

    # Append new entry
    new_crontab = "\n".join(cleaned).rstrip() + "\n" + cron_line + "\n"
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp:
        tmp.write(new_crontab)
        tmp_path = tmp.name
    try:
        proc = subprocess.run(["crontab", tmp_path], capture_output=True, text=True)
        return proc.returncode == 0, proc.stderr
    finally:
        os.unlink(tmp_path)


def install_cron_remote(cron_line, name, host, user):
    """Install cron on remote host via SSH."""
    # Build a shell script that does the same thing remotely
    escaped_line = cron_line.replace("'", "'\\''")
    remote_script = f"""
current=$(crontab -l 2>/dev/null || echo '')
cleaned=$(echo "$current" | awk -v name='{name}' '
    /^# specter-managed name=/ {{ skip = ($0 ~ name); next }}
    skip == 1 {{ skip = 0; next }}
    {{ print }}
')
echo "$cleaned" > /tmp/cron_new
echo '{escaped_line}' >> /tmp/cron_new
crontab /tmp/cron_new
rm -f /tmp/cron_new
"""
    proc = subprocess.run(
        ["ssh", "-o", "StrictHostKeyChecking=no", f"{user}@{host}", "bash -s"],
        input=remote_script, capture_output=True, text=True, timeout=15,
    )
    return proc.returncode == 0, proc.stderr


if target == "phantom":
    ok, err = install_cron_local(cron_line, name)
elif target == "baza":
    ok, err = install_cron_remote(
        cron_line, name,
        os.environ.get("BAZA_MAIN_HOST", "100.127.118.103"),
        os.environ.get("BAZA_MAIN_USER", "switchhacker"),
    )
else:
    print(f"Error: target must be 'phantom' or 'baza' (got '{target}')")
    sys.exit(1)

if ok:
    log_creation("specter_voss", "cron", name, True, {"target": target, "schedule": schedule})
    print(f"✓ Cron '{name}' installed on {target}")
    print(f"   Schedule: {schedule}")
    print(f"   Command: {command[:100]}")
else:
    print(f"✗ Install failed: {err}")
    sys.exit(1)
