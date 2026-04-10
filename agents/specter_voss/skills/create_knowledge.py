#!/usr/bin/env python3
"""
Skill: create_knowledge
Specter creates/updates empire_knowledge entries. Approval-gated.
Empire knowledge is shared across ALL agents (unlike agent_memory which is per-agent).

Usage:
    SKILL_ARGS='{
        "key": "current_bid_rate_philadelphia",
        "value": "$150/sqft for kitchen remodels",
        "category": "pricing"
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
key = args.get("key", "").strip()
value = args.get("value", "").strip()
category = args.get("category", "general").strip()

if not key:
    print("Error: 'key' is required")
    sys.exit(1)

details = f"Key: {key}\nCategory: {category}\nShared with: ALL AGENTS\nValue: {value[:500]}"
approved = request_approval(
    category="knowledge",
    title=f"Empire knowledge: {key}",
    details=details,
    timeout=300,
)

if not approved:
    log_creation("specter_voss", "knowledge", key, False)
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
        """INSERT INTO empire_knowledge (key, value, category, updated_by, updated_at)
           VALUES (%s, %s, %s, %s, NOW())
           ON CONFLICT (key) DO UPDATE
           SET value = EXCLUDED.value, category = EXCLUDED.category,
               updated_by = EXCLUDED.updated_by, updated_at = NOW()""",
        (key, value, category, "specter_voss"),
    )
    conn.commit()
    cur.close()
    conn.close()

    # Also publish event so all agents know
    try:
        import redis as _r
        r = _r.Redis(host="100.127.118.103", port=6379)
        r.publish("baza:events:knowledge_updated",
                  json.dumps({"key": key, "category": category, "updated_by": "specter_voss"}))
        r.close()
    except Exception:
        pass

    log_creation("specter_voss", "knowledge", key, True, {"category": category})
    print(f"✓ Empire knowledge saved: {key}")
    print(f"   All 9 agents can now read this via get_empire_knowledge('{key}')")
except Exception as e:
    print(f"✗ DB error: {e}")
    sys.exit(1)
