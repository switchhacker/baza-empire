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
import datetime
import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS_YAML = os.path.join(REPO_ROOT, "config", "agents.yaml")
PYTHON_BIN = os.path.join(REPO_ROOT, "venv", "bin", "python")
MARKER_PREFIX = "# baza-empire-managed name="

# ── systemd target (Task 11) ────────────────────────────────────────────
# Per-cron user timers, written under ~/.config/systemd/user/ by default.
# Overridable via BAZA_SYSTEMD_USER_DIR so tests never touch the real dir.
SYSTEMD_USER_DIR = os.environ.get("BAZA_SYSTEMD_USER_DIR") or os.path.expanduser(
    "~/.config/systemd/user"
)
UNIT_PREFIX = "baza-cron-"
ALERT_TEMPLATE_NAME = "baza-cron-alert@.service"
ALERT_TEMPLATE_SRC = os.path.join(REPO_ROOT, "configs", "systemd-user", ALERT_TEMPLATE_NAME)
SECRETS_ENV = os.path.join(REPO_ROOT, "configs", "secrets.env")

WEEKDAY_NAMES = {0: "Sun", 1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}


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


# ── systemd target (Task 11) ────────────────────────────────────────────

def build_systemd_task(agent_id, task):
    """Build the systemd-target field dict for one declared task.

    Reuses build_cron_line() for the unit basename/schedule so the two
    targets never disagree on naming, then carries the raw script/log/
    timeout_min fields render_units() needs (build_cron_line() only
    returns the flattened shell command, not these individually).
    """
    name, schedule, _cmd = build_cron_line(agent_id, task)
    script = task["script"]
    log = task.get("log", f"logs/{agent_id}_{task['name']}.log")
    timeout_min = task.get("timeout_min", 30)
    return {
        "name": name,
        "schedule": schedule,
        "script": script,
        "log": log,
        "timeout_min": timeout_min,
        "agent_id": agent_id,
        "task_name": task["name"],
    }


def _cron_time_field(field, width=2):
    """Convert one cron minute/hour field to a systemd OnCalendar time
    sub-expression. Supports '*', plain integers, 'a-b' ranges, and
    '*/n' / 'a-b/n' step expressions. Anything else -> ValueError."""
    if field == "*":
        return "*"
    if "/" in field:
        base, _, step = field.partition("/")
        if not step.isdigit():
            raise ValueError(f"Unsupported cron field step: {field!r}")
        if base == "*":
            start = f"{0:0{width}d}"
        elif "-" in base:
            lo, _, hi = base.partition("-")
            if not (lo.isdigit() and hi.isdigit()):
                raise ValueError(f"Unsupported cron field: {field!r}")
            start = f"{int(lo):0{width}d}..{int(hi):0{width}d}"
        elif base.isdigit():
            start = f"{int(base):0{width}d}"
        else:
            raise ValueError(f"Unsupported cron field: {field!r}")
        return f"{start}/{step}"
    if "-" in field:
        lo, _, hi = field.partition("-")
        if not (lo.isdigit() and hi.isdigit()):
            raise ValueError(f"Unsupported cron field: {field!r}")
        return f"{int(lo):0{width}d}..{int(hi):0{width}d}"
    if field.isdigit():
        return f"{int(field):0{width}d}"
    raise ValueError(f"Unsupported cron field: {field!r}")


def _cron_dow_field(field):
    """Convert a cron day-of-week field to a systemd weekday prefix (or
    None for '*'). Supports '*', a single 0-7 digit (0 and 7 both mean
    Sunday, matching standard cron), and 'a-b' ranges."""
    if field == "*":
        return None
    if "-" in field:
        lo, _, hi = field.partition("-")
        if not (lo.isdigit() and hi.isdigit()):
            raise ValueError(f"Unsupported cron dow field: {field!r}")
        lo_i, hi_i = int(lo), int(hi)
        if lo_i not in WEEKDAY_NAMES or hi_i not in WEEKDAY_NAMES:
            raise ValueError(f"Unsupported cron dow field: {field!r}")
        return f"{WEEKDAY_NAMES[lo_i]}..{WEEKDAY_NAMES[hi_i]}"
    if field.isdigit():
        i = int(field)
        if i not in WEEKDAY_NAMES:
            raise ValueError(f"Unsupported cron dow field: {field!r}")
        return WEEKDAY_NAMES[i]
    raise ValueError(f"Unsupported cron dow field: {field!r}")


def cron_to_oncalendar(expr: str) -> str:
    """Convert a standard 5-field cron expression to a systemd OnCalendar=
    expression. Supports the subset actually used by config/agents.yaml:
    '*'/digit/'a-b'/'*/n'/'a-b/n' minute+hour fields, '*' or a digit or an
    'a-b' range day-of-week field, and '*' day-of-month/month (no support
    for day-of-month or month restrictions, comma lists, or step ranges on
    day-of-week — raises ValueError for those).

    Examples (see task-11-brief.md):
      "0 */6 * * *"     -> "*-*-* 00/6:00:00"
      "45 */6 * * *"    -> "*-*-* 00/6:45:00"
      "0 9 * * *"       -> "*-*-* 09:00:00"
      "0 7 * * 1"       -> "Mon *-*-* 07:00:00"
      "0 6 * * 3"       -> "Wed *-*-* 06:00:00"
      "0 5-19/2 * * *"  -> "*-*-* 05..19/2:00:00"
      "15 6 * * 1-5"    -> "Mon..Fri *-*-* 06:15:00"
      "*/30 * * * *"    -> "*-*-* *:00/30:00"
    """
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(f"Unsupported cron expression (expected 5 fields): {expr!r}")
    minute, hour, dom, month, dow = parts
    if dom != "*" or month != "*":
        raise ValueError(
            f"Unsupported cron expression (day-of-month/month restrictions unsupported): {expr!r}"
        )
    hour_c = _cron_time_field(hour, width=2)
    minute_c = _cron_time_field(minute, width=2)
    dow_prefix = _cron_dow_field(dow)
    time_expr = f"*-*-* {hour_c}:{minute_c}:00"
    return f"{dow_prefix} {time_expr}" if dow_prefix else time_expr


def render_units(task: dict) -> tuple:
    """Render (service_text, timer_text) for one systemd-target cron task.

    `task` is the dict shape returned by build_systemd_task(): name,
    schedule, script, log, timeout_min, agent_id, task_name.
    """
    name = task["name"]
    schedule = task["schedule"]
    script = task["script"]
    log = task["log"]
    timeout_min = task.get("timeout_min", 30)
    agent_id = task.get("agent_id", "")
    task_name = task.get("task_name", name)

    abs_script = os.path.join(REPO_ROOT, script)
    abs_log = os.path.join(REPO_ROOT, log)
    interpreter = "bash" if script.endswith(".sh") else PYTHON_BIN
    exec_start = f"{interpreter} {abs_script}"
    oncalendar = cron_to_oncalendar(schedule)
    runtime_max_sec = int(timeout_min) * 60

    service_text = (
        "[Unit]\n"
        f"Description=baza-empire cron: {name} ({agent_id}/{task_name})\n"
        "OnFailure=baza-cron-alert@%n.service\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"WorkingDirectory={REPO_ROOT}\n"
        f"ExecStart={exec_start}\n"
        f"StandardOutput=append:{abs_log}\n"
        "StandardError=inherit\n"
        f"RuntimeMaxSec={runtime_max_sec}\n"
        f"EnvironmentFile={SECRETS_ENV}\n"
    )
    timer_text = (
        "[Unit]\n"
        f"Description=baza-empire cron timer: {name}\n"
        "\n"
        "[Timer]\n"
        f"OnCalendar={oncalendar}\n"
        "Persistent=true\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )
    return service_text, timer_text


def systemd_unit_paths(name, systemd_dir=None):
    d = systemd_dir or SYSTEMD_USER_DIR
    return (
        os.path.join(d, f"{UNIT_PREFIX}{name}.service"),
        os.path.join(d, f"{UNIT_PREFIX}{name}.timer"),
    )


def _read_file(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def _existing_managed_units(systemd_dir):
    """Names (without the baza-cron- prefix/.service suffix) of every
    managed unit pair currently on disk, excluding the alert template."""
    found = set()
    if not os.path.isdir(systemd_dir):
        return found
    for fname in os.listdir(systemd_dir):
        if fname == ALERT_TEMPLATE_NAME:
            continue
        if fname.startswith(UNIT_PREFIX) and fname.endswith(".service"):
            found.add(fname[len(UNIT_PREFIX):-len(".service")])
    return found


def plan_systemd(declared, systemd_dir=None):
    """Compute the systemd sync plan without touching disk (beyond reads).

    Returns (entries, plan, alert_status, errors):
      entries      {name: {"task": ..., "service_text": ..., "timer_text": ...}}
      plan         {name: status} status in {'=', '+', '~', '-'}
      alert_status status of the baza-cron-alert@.service template, or
                   None if ALERT_TEMPLATE_SRC is missing
      errors       [(name, error_message)] for declared tasks whose
                   schedule couldn't be converted (skipped from entries/plan)
    """
    d = systemd_dir or SYSTEMD_USER_DIR
    entries = {}
    errors = []
    for agent_id, task in declared:
        t = build_systemd_task(agent_id, task)
        try:
            service_text, timer_text = render_units(t)
        except ValueError as e:
            errors.append((t["name"], str(e)))
            continue
        entries[t["name"]] = {"task": t, "service_text": service_text, "timer_text": timer_text}

    existing_names = _existing_managed_units(d)
    plan = {}
    for name, e in entries.items():
        svc_path, timer_path = systemd_unit_paths(name, d)
        cur_svc = _read_file(svc_path)
        cur_timer = _read_file(timer_path)
        if cur_svc is None and cur_timer is None:
            plan[name] = "+"
        elif cur_svc != e["service_text"] or cur_timer != e["timer_text"]:
            plan[name] = "~"
        else:
            plan[name] = "="
    for name in existing_names:
        if name not in entries:
            plan[name] = "-"

    alert_src = _read_file(ALERT_TEMPLATE_SRC)
    if alert_src is None:
        alert_status = None
    else:
        alert_cur = _read_file(os.path.join(d, ALERT_TEMPLATE_NAME))
        if alert_cur is None:
            alert_status = "+"
        elif alert_cur != alert_src:
            alert_status = "~"
        else:
            alert_status = "="

    return entries, plan, alert_status, errors


def default_runner(cmd, input=None):
    """Real subprocess runner — the default for apply_systemd()/
    remove_crontab_managed_lines(). Tests inject a fake with the same
    `runner(cmd, input=None) -> CompletedProcess-like` signature so no
    test ever shells out to the real systemctl/crontab."""
    return subprocess.run(cmd, input=input, capture_output=True, text=True)


def remove_crontab_managed_lines(names, runner=None, today=None):
    """Strip the given managed-block names out of the live crontab and,
    if any were present, replace them with a single comment marking the
    systemd migration. No-op (returns {"removed": [], "date": None}) if
    none of `names` are currently in the crontab.
    """
    run = runner or default_runner
    names_set = set(names)
    read = run(["crontab", "-l"])
    raw = read.stdout if getattr(read, "returncode", 0) == 0 and read.stdout else ""
    lines = raw.splitlines() if raw else []

    out_lines = []
    removed_names = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith(MARKER_PREFIX):
            name = line[len(MARKER_PREFIX):].strip()
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            block_end = j + 1 if j < len(lines) else len(lines)
            if name in names_set:
                removed_names.append(name)
                i = block_end
                continue
            out_lines.append(line)
            i += 1
            continue
        out_lines.append(line)
        i += 1

    if not removed_names:
        return {"removed": [], "date": None}

    while out_lines and not out_lines[-1].strip():
        out_lines.pop()

    date_str = today or datetime.date.today().isoformat()
    out_lines.append("")
    out_lines.append(f"# baza-empire-managed migrated-to-systemd {date_str}")
    new_crontab = "\n".join(out_lines) + "\n"

    write = run(["crontab", "-"], input=new_crontab)
    ok = getattr(write, "returncode", 1) == 0
    return {"removed": sorted(removed_names), "date": date_str, "ok": ok}


def apply_systemd(declared, systemd_dir=None, runner=None, today=None):
    """Write/update/remove systemd user units for the declared tasks,
    daemon-reload, enable --now each timer, install the alert template,
    then strip the migrated entries out of the crontab.

    `runner` is injected into every systemctl/crontab call (see
    default_runner) so tests never touch the real systemd/crontab.
    """
    d = systemd_dir or SYSTEMD_USER_DIR
    run = runner or default_runner

    entries, plan, alert_status, errors = plan_systemd(declared, d)
    os.makedirs(d, exist_ok=True)

    written = []
    for name, e in entries.items():
        svc_path, timer_path = systemd_unit_paths(name, d)
        with open(svc_path, "w") as f:
            f.write(e["service_text"])
        with open(timer_path, "w") as f:
            f.write(e["timer_text"])
        written.append(name)

    removed = []
    for name in _existing_managed_units(d):
        if name in entries:
            continue
        svc_path, timer_path = systemd_unit_paths(name, d)
        run(["systemctl", "--user", "disable", "--now", f"{UNIT_PREFIX}{name}.timer"])
        for p in (svc_path, timer_path):
            if os.path.isfile(p):
                os.remove(p)
        removed.append(name)

    alert_src = _read_file(ALERT_TEMPLATE_SRC)
    if alert_src is not None:
        with open(os.path.join(d, ALERT_TEMPLATE_NAME), "w") as f:
            f.write(alert_src)

    run(["systemctl", "--user", "daemon-reload"])
    for name in written:
        run(["systemctl", "--user", "enable", "--now", f"{UNIT_PREFIX}{name}.timer"])

    crontab_result = remove_crontab_managed_lines(sorted(entries.keys()), runner=run, today=today)

    return {
        "written": sorted(written),
        "removed": sorted(removed),
        "errors": errors,
        "alert_installed": alert_src is not None,
        "crontab": crontab_result,
    }


def main_systemd(declared, args):
    """--target systemd entry point: dry-run diff by default, --apply to write."""
    entries, plan, alert_status, errors = plan_systemd(declared)

    print(f"Declared tasks: {len(declared)}")
    print(f"systemd user dir: {SYSTEMD_USER_DIR}")
    print()

    drift = 0
    for name in sorted(plan.keys()):
        status = plan[name]
        sched = entries[name]["task"]["schedule"] if name in entries else "?"
        unit = f"{UNIT_PREFIX}{name}"
        if status == "=":
            print(f"  [=] {unit:50} {sched}")
        elif status == "+":
            drift += 1
            print(f"  [+] {unit:50} {sched}   ADD")
        elif status == "~":
            drift += 1
            print(f"  [~] {unit:50} {sched}   UPDATE")
        elif status == "-":
            drift += 1
            print(f"  [-] {unit:50}   REMOVE (no longer declared)")

    if alert_status == "+":
        drift += 1
        print(f"  [+] {ALERT_TEMPLATE_NAME:50} ADD (alert template)")
    elif alert_status == "~":
        drift += 1
        print(f"  [~] {ALERT_TEMPLATE_NAME:50} UPDATE (alert template)")
    elif alert_status == "=":
        print(f"  [=] {ALERT_TEMPLATE_NAME:50} (alert template)")
    elif alert_status is None:
        print(f"  [!] {ALERT_TEMPLATE_NAME:50} MISSING SOURCE at {ALERT_TEMPLATE_SRC}")

    if errors:
        print()
        print("Unsupported schedules (cannot convert to OnCalendar= — fix agents.yaml or skip):")
        for name, msg in errors:
            print(f"  [x] {name}: {msg}")
        drift += len(errors)

    print()
    if drift == 0:
        print("✓ in sync — no changes needed")
        return 0

    if args.check:
        print(f"✗ {drift} drift item(s) — failing --check")
        return 1

    if not args.apply:
        print(f"{drift} change(s) pending. Re-run with --apply --target systemd to write units.")
        return 0

    result = apply_systemd(declared)
    print(
        f"✓ wrote {len(result['written'])} unit pair(s), removed {len(result['removed'])} "
        f"orphaned pair(s); daemon-reload + enable --now applied"
    )
    if result["crontab"]["removed"]:
        print(
            f"✓ removed {len(result['crontab']['removed'])} migrated crontab block(s), "
            f"left marker comment dated {result['crontab']['date']}"
        )
    if result["errors"]:
        # Non-zero so deploy scripts gating on exit code can't mistake a
        # partial migration (unconvertible schedules left on crontab) for success.
        print(f"✗ {len(result['errors'])} schedule(s) skipped — see above")
        return 1
    return 0


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
    ap.add_argument(
        "--target",
        choices=["crontab", "systemd"],
        default="crontab",
        help="crontab (default, rollback-safe) or systemd (per-cron user timers)",
    )
    args = ap.parse_args()

    declared = load_declared_tasks(args.agent)

    if args.target == "systemd":
        return main_systemd(declared, args)

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
