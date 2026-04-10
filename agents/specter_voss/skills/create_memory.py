#!/usr/bin/env python3
"""
Skill: create_memory
Specter creates/updates agent_memory entries. Approval-gated.
Stores facts Specter has learned about an agent's domain or state.

Usage:
    SKILL_ARGS='{
        "agent": "claw_batto",
        "key": "preferred_deploy_branch",
        "value": "main",
        "category": "preferences"
    }'
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _specter_approval import request_approval, log_creation

try:
    import psycopg2
except ImportError:
    print("psycopg2 not installed")
    sys.exit(1)

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
agent = args.get("agent", "").strip()
key = args.get("key", "").strip()
value = args.get("value", "").strip()
category = args.get("category", "general").strip()

if not agent or not key:
    print("Error: 'agent' and 'key' are required")
    sys.exit(1)

details = f"Agent: {agent}\nKey: {key}\nCategory: {category}\nValue: {value[:300]}"
approved = request_approval(
    category="memory",
    title=f"Memory for {agent}: {key}",
    details=details,
    timeout=300,
)

if not approved:
    log_creation("specter_voss", "memory", f"{agent}:{key}", False)
    print("DENIED")
    sys.exit(0)

try:
    conn = psycopg2.connect(
        host=os.environ.get("BAZA_DB_HOST", "100.127.118.103"),
        port=int(os.environ.get("BAZA_DB_PORT", "5432")),
        dbname=os.environ.get("BAZA_DB_NAME", "baza_agents"),
        user=os.environ.get("BAZA_DB_USER", "switchhacker"),
        password=os.environ.get("DB_PASSWORD", "baza2026"),
    )
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO agent_memory (agent_id, key, value, category, updated_at)
           VALUES (%s, %s, %s, %s, NOW())
           ON CONFLICT (agent_id, key) DO UPDATE
           SET value = EXCLUDED.value, category = EXCLUDED.category, updated_at = NOW()""",
        (agent, key, value, category),
    )
    conn.commit()
    cur.close()
    conn.close()
    log_creation("specter_voss", "memory", f"{agent}:{key}", True, {"category": category})
    print(f"✓ Memory saved: {agent} / {key} = {value[:80]}")
except Exception as e:
    print(f"✗ DB error: {e}")
    sys.exit(1)
