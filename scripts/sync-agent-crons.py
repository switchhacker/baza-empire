#!/usr/bin/env python3
"""
sync-agent-crons.py — Graft 6 reconciler
─────────────────────────────────────────
Reads `scheduled_tasks` blocks from config/agents.yaml, walks each agent's
crons/ directory, and reconciles the result with the user's crontab. Each
managed entry is tagged with a `# baza-empire-managed name=<id>` marker so
this script can find and update only the lines it owns — manual entries
in your crontab are left alone.

Usage:
    scripts/sync-agent-crons.py                # dry-run (default — shows diff)
    scripts/sync-agent-crons.py --apply        # writes the new crontab
    scripts/sync-agent-crons.py --check        # exit 1 if drift exists (CI hook)
    scripts/sync-agent-crons.py --agent phil   # filter to one agent

Output legend:
    [=]  in sync (declared, in crontab, schedules match)
    [+]  declared but missing from crontab — will be added on --apply
    [~]  schedule changed — will be updated on --apply
    [-]  in crontab but no longer declared — will be removed on --apply
    [?]  cron script exists in agents/<id>/crons/ but not declared — informational
"""
import os
import sys
import subprocess
import argparse
import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS_YAML = os.path.join(REPO_ROOT, "config", "agents.yaml")
PYTHON_BIN = os.path.join(REPO_ROOT, "venv", "bin", "python")
MARKER_PREFIX = "# baza-empire-managed name="


def load_declared_tasks(agent_filter=None):
    """Read agents.yaml and return [(agent_id, task_dict), ...]"""
    if not os.path.isfile(AGENTS_YAML):
        return []
    with open(AGENTS_YAML) as f:
        data = yaml.safe_load(f) or {}
    tasks = []
    agents = data.get("agents", data)  # support both flat and nested layouts
    for agent_id, cfg in agents.items():
        if agent_filter and agent_filter not in agent_id:
            continue
        if not isinstance(cfg, dict):
            continue
        for t in cfg.get("scheduled_tasks", []) or []:
            if not t.get("enabled", True):
                continue
            tasks.append((agent_id, t))
    return tasks


def discover_filesystem_crons(agent_filter=None):
    """Find all agents/<id>/crons/*.py — used to flag undeclared scripts."""
    found = []
    agents_dir = os.path.join(REPO_ROOT, "agents")
    if not os.path.isdir(agents_dir):
        return found
    for agent_id in sorted(os.listdir(agents_dir)):
        if agent_filter and agent_filter not in agent_id:
            continue
        crons_dir = os.path.join(agents_dir, agent_id, "crons")
        if not os.path.isdir(crons_dir):
            continue
        for fname in sorted(os.listdir(crons_dir)):
            if fname.endswith(".py") and not fname.startswith("_"):
                rel = f"agents/{agent_id}/crons/{fname}"
                found.append((agent_id, fname[:-3], rel))
    return found


def read_crontab():
    """Return list of (marker_name, schedule, command, full_block) tuples for managed entries."""
    try:
        out = subprocess.check_output(["crontab", "-l"], text=True, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        return [], ""
    lines = out.splitlines()
    managed = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith(MARKER_PREFIX):
            name = line[len(MARKER_PREFIX):].strip()
            # next non-blank line is the cron entry (may be commented-out = disabled)
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines):
                entry = lines[j]
                stripped = entry.lstrip("#").strip()
                parts = stripped.split(None, 5)
                if len(parts) >= 6:
                    schedule = " ".join(parts[:5])
                    command = parts[5]
                    managed.append({
                        "name": name,
                        "schedule": schedule,
                        "command": command,
                        "enabled": not entry.startswith("#"),
                        "raw": entry,
                    })
            i = j + 1
        else:
            i += 1
    return managed, out


def build_cron_line(agent_id, task):
    """Render a managed crontab block for one declared task.

    - Every invocation is wrapped in `timeout {timeout_min}m` (optional
      per-task `timeout_min` key, default 30) so a hung cron can't run
      forever and starve the crontab. `timeout` wraps the interpreter
      invocation, not the `cd` — matches `cd <fw> && timeout 30m <bin> <script> >> log 2>&1`.
    - `.sh` scripts run via `bash <script>`; everything else runs via the
      venv's `PYTHON_BIN`.
    """
    name = f"{agent_id}_{task['name']}"
    schedule = task["schedule"]
    script = task["script"]
    log = task.get("log", f"logs/{agent_id}_{task['name']}.log")
    timeout_min = task.get("timeout_min", 30)
    abs_script = os.path.join(REPO_ROOT, script)
    abs_log = os.path.join(REPO_ROOT, log)
    interpreter = "bash" if script.endswith(".sh") else PYTHON_BIN
    cmd = (
        f"cd {REPO_ROOT} && timeout {timeout_min}m {interpreter} {abs_script} "
        f">> {abs_log} 2>&1"
    )
    return name, schedule, cmd


def diff(declared, current):
    """Return dict of {name: status} where status ∈ {=, +, ~, -}."""
    declared_map = {}
    for agent_id, task in declared:
        name, schedule, cmd = build_cron_line(agent_id, task)
        declared_map[name] = {"schedule": schedule, "command": cmd, "agent": agent_id, "task": task}

    current_map = {c["name"]: c for c in current}

    statuses = {}
    for name, d in declared_map.items():
        if name not in current_map:
            statuses[name] = ("+", d, None)
        elif current_map[name]["schedule"] != d["schedule"] or current_map[name]["command"] != d["command"]:
            statuses[name] = ("~", d, current_map[name])
        else:
            statuses[name] = ("=", d, current_map[name])
    for name, c in current_map.items():
        if name not in declared_map:
            statuses[name] = ("-", None, c)
    return statuses


def render_crontab(existing_raw, declared, removed_names):
    """Build the new crontab text:
       - drop any managed blocks (we'll re-emit them)
       - keep all unmanaged lines as-is
       - append fresh managed blocks for every declared task
    """
    lines = existing_raw.splitlines() if existing_raw else []
    out_lines = []
    skip_until_blank = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith(MARKER_PREFIX):
            # skip the marker + next non-blank line
            i += 1
            while i < len(lines) and not lines[i].strip():
                i += 1
            if i < len(lines):
                i += 1
            continue
        out_lines.append(line)
        i += 1

    # trim trailing blank lines
    while out_lines and not out_lines[-1].strip():
        out_lines.pop()

    out_lines.append("")
    out_lines.append("# ── baza-empire managed cron blocks (sync-agent-crons.py) ──")
    for agent_id, task in declared:
        name, schedule, cmd = build_cron_line(agent_id, task)
        out_lines.append(f"{MARKER_PREFIX}{name}")
        out_lines.append(f"{schedule} {cmd}")
        out_lines.append("")
    return "\n".join(out_lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Write the reconciled crontab")
    ap.add_argument("--check", action="store_true", help="Exit 1 if drift exists (no writes)")
    ap.add_argument("--agent", help="Filter to one agent (substring match)")
    args = ap.parse_args()

    declared = load_declared_tasks(args.agent)
    fs_crons = discover_filesystem_crons(args.agent)
    current, raw = read_crontab()
    statuses = diff(declared, current)

    print(f"Declared tasks: {len(declared)}")
    print(f"Currently managed in crontab: {len(current)}")
    print(f"Filesystem cron scripts: {len(fs_crons)}")
    print()

    drift = 0
    for name in sorted(statuses.keys()):
        status, decl, cur = statuses[name]
        if status == "=":
            print(f"  [=] {name:40} {decl['schedule']}")
        elif status == "+":
            drift += 1
            print(f"  [+] {name:40} {decl['schedule']}   ADD")
        elif status == "~":
            drift += 1
            print(f"  [~] {name:40} {cur['schedule']} → {decl['schedule']}   UPDATE")
        elif status == "-":
            drift += 1
            print(f"  [-] {name:40} {cur['schedule']}   REMOVE")

    # Filesystem-only scripts (not declared, not in crontab)
    declared_scripts = {t["script"] for _, t in declared}
    crontab_cmds = " ".join(c["command"] for c in current)
    print()
    print("Undeclared filesystem cron scripts:")
    any_undeclared = False
    for agent_id, task_name, rel in fs_crons:
        if rel in declared_scripts:
            continue
        in_crontab = rel in crontab_cmds
        marker = "in_crontab" if in_crontab else "orphaned"
        print(f"  [?] {agent_id}/{task_name:30} ({marker})  {rel}")
        any_undeclared = True
    if not any_undeclared:
        print("  (none)")

    print()
    if drift == 0:
        print("✓ in sync — no changes needed")
        return 0

    if args.check:
        print(f"✗ {drift} drift item(s) — failing --check")
        return 1

    if not args.apply:
        print(f"{drift} change(s) pending. Re-run with --apply to write crontab.")
        return 0

    new_crontab = render_crontab(raw, declared, removed_names=[])
    proc = subprocess.run(
        ["crontab", "-"],
        input=new_crontab,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        print(f"crontab write failed: {proc.stderr}")
        return 2
    print(f"✓ wrote {len(declared)} managed entries to crontab")
    return 0


if __name__ == "__main__":
    sys.exit(main())
