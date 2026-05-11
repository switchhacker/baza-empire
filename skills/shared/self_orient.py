#!/usr/bin/env python3
"""
Self-Orient — topic-scoped situational refresh for agents.

An agent invokes this skill mid-conversation when it needs to check the
current state of something it doesn't already have in its boot header.

Args (JSON in SKILL_ARGS env var):
    {}                          → return the full boot snapshot
    {"topic": "mining"}         → return EMPIRE_STATE.md TOPIC: mining block
    {"topic": "myself"}         → return calling agent's dir/skills/journal
    {"topic": "<unknown>"}      → grep fallback across EMPIRE_STATE.md + baza-map.md

Calling agent id resolution:
    BAZA_AGENT_ID env var (preferred — SkillsEngine sets this)
    AGENT_ID env var (fallback)
    "unknown" if neither set
"""
from __future__ import annotations

import json
import os
import sys

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, FRAMEWORK_DIR)

from core import empire_state


def _agent_id() -> str:
    return (
        os.environ.get("BAZA_AGENT_ID")
        or os.environ.get("AGENT_ID")
        or "unknown"
    )


def _myself_block(agent_id: str) -> str:
    """Return identity + dir tree + skills + last 5 journal entries."""
    role = empire_state.get_agent_role(agent_id)
    skills = empire_state.get_agent_skills(agent_id)
    agent_dir = os.path.join(FRAMEWORK_DIR, "agents", agent_id)

    tree: list[str] = []
    if os.path.isdir(agent_dir):
        for entry in sorted(os.listdir(agent_dir)):
            full = os.path.join(agent_dir, entry)
            if entry.startswith(".") or entry == "__pycache__":
                continue
            if os.path.isdir(full):
                subs = []
                try:
                    for sub in sorted(os.listdir(full)):
                        if sub.startswith(".") or sub == "__pycache__":
                            continue
                        subs.append(sub)
                except Exception:
                    pass
                tree.append(f"  {entry}/  ({', '.join(subs[:8])})")
            else:
                tree.append(f"  {entry}")

    # Last 5 journal entries
    journal_lines: list[str] = []
    try:
        from core.context_db import journal_get
        rows = journal_get(agent_id=agent_id, limit=5)
        for r in rows:
            ts = r.get("date") or "?"
            ttype = r.get("type") or "?"
            desc = (r.get("description") or "")[:80]
            verified = "✓" if r.get("verified", True) else "⚠UNVERIFIED"
            journal_lines.append(f"  [{ts}] {ttype}: {desc} {verified}")
    except Exception as e:
        journal_lines.append(f"  (journal read failed: {e})")

    parts = [
        f"YOU ARE: {agent_id} — {role}",
        f"\nYOUR DIRECTORY (agents/{agent_id}/):",
        *(tree or ["  (empty or missing)"]),
        f"\nYOUR SKILLS ({len(skills)}):",
        "  " + ", ".join(skills) if skills else "  (none registered)",
        "\nYOUR LAST 5 JOURNAL ENTRIES:",
        *(journal_lines or ["  (none)"]),
    ]
    return "\n".join(parts)


def _full_snapshot(agent_id: str) -> str:
    """Same content as the boot-time header, regenerated fresh."""
    return empire_state.build_header(agent_id)


def main() -> int:
    try:
        args = json.loads(os.environ.get("SKILL_ARGS", "{}") or "{}")
    except Exception:
        args = {}

    agent_id = _agent_id()
    topic = (args.get("topic") or "").strip()

    if not topic:
        print(_full_snapshot(agent_id))
        return 0

    if topic.lower() == "myself":
        print(_myself_block(agent_id))
        return 0

    block = empire_state.lookup_topic(topic, with_session_log=True)
    print(block)
    return 0


if __name__ == "__main__":
    sys.exit(main())
