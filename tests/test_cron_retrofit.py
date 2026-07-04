"""Test for Task 8 of the cron-improvements plan: the mechanical retrofit of
every declared agent cron script to (a) wrap its entrypoint in
agents.cron_helpers.cron_run(name) for heartbeat tracking, and (b) route its
outbound report(s) through send_report()/send_alert() instead of a bare
send_telegram() call.

Source-scans config/agents.yaml scheduled_tasks (the single source of truth
for which crons exist) rather than hardcoding the file list, so this test
also catches any *newly declared* cron that forgets the retrofit. Scripts
declared with a non-.py extension (e.g. a future .sh entry) are skipped —
cron_run()/send_report() are Python-only helpers.
"""
import os

import pytest
import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS_YAML = os.path.join(REPO_ROOT, "config", "agents.yaml")


def _declared_py_crons():
    """Every enabled scheduled_task across all agents whose script ends in
    .py. Returns [(agent_id, task_name, abs_script_path), ...]."""
    with open(AGENTS_YAML) as f:
        data = yaml.safe_load(f) or {}
    agents = data.get("agents", data) or {}
    out = []
    for agent_id, cfg in agents.items():
        if not isinstance(cfg, dict):
            continue
        for t in cfg.get("scheduled_tasks", []) or []:
            if not t.get("enabled", True):
                continue
            script = t.get("script", "")
            if not script.endswith(".py"):
                continue
            out.append((agent_id, t.get("name"), os.path.join(REPO_ROOT, script)))
    return out


def test_all_declared_crons_use_cron_run():
    declared = _declared_py_crons()
    assert declared, "no enabled .py scheduled_tasks found in config/agents.yaml -- parsing likely broken"

    failures = []
    for agent_id, name, path in declared:
        if not os.path.isfile(path):
            failures.append(f"{agent_id}/{name}: declared script missing on disk: {path}")
            continue
        with open(path) as f:
            src = f.read()
        if "cron_run(" not in src:
            failures.append(f"{agent_id}/{name} ({path}): missing cron_run(...) heartbeat wrap")
        if (
            "send_report(" not in src
            and "send_alert(" not in src
            and "retrofit-exempt:" not in src
        ):
            # A cron may legitimately have no direct Telegram send (pure
            # maintenance, or delivery via another mechanism like the
            # suggest_action approval skill) -- but it must SAY so with a
            # "# retrofit-exempt: <reason>" comment, not slip through.
            failures.append(f"{agent_id}/{name} ({path}): missing send_report(...)/send_alert(...) routed send")

    assert not failures, "cron retrofit gaps:\n" + "\n".join(failures)
