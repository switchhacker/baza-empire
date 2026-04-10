#!/usr/bin/env python3
"""
Skill: team_pulse
Specter reads the unified team_activity view to see what agents DID recently,
AND reads Redis heartbeats (baza:heartbeat:<agent_id>) to see which agents ARE
RUNNING right now. Without the heartbeat check this skill confuses "idle" with
"dead" and triggers false-alarm restart proposals that could clobber a healthy
system — DO NOT REMOVE THE HEARTBEAT CHECK.

Usage:
    SKILL_ARGS='{}'                                    # last 4 hours, all agents
    SKILL_ARGS='{"hours":24}'                          # last 24h
    SKILL_ARGS='{"agent":"claw_batto","hours":12}'     # one agent deep dive
    SKILL_ARGS='{"kind":"chat"}'                       # only user/agent chats
    SKILL_ARGS='{"kind":"action","hours":48}'          # only actions/skills
    SKILL_ARGS='{"limit":50,"format":"detail"}'        # more detail, 50 rows
"""
import os
import json
import sys
import time
from datetime import datetime

try:
    import psycopg2
except ImportError:
    print("psycopg2 not installed")
    sys.exit(1)

try:
    import redis as _redis
    _R_OK = True
except ImportError:
    _R_OK = False


def _check_heartbeats(agent_ids):
    """Return {agent_id: {'alive': bool, 'status': str, 'age_s': int, 'model': str}}
    by reading Redis keys set by each agent's own heartbeat loop."""
    out = {}
    if not _R_OK:
        for a in agent_ids:
            out[a] = {"alive": None, "status": "heartbeat_unavailable", "age_s": None, "model": None}
        return out
    try:
        r = _redis.Redis(host="localhost", port=6379, decode_responses=True, socket_timeout=3)
        now = int(time.time())
        for aid in agent_ids:
            hb_raw = r.get(f"baza:heartbeat:{aid}")
            if not hb_raw:
                out[aid] = {"alive": False, "status": "no_heartbeat", "age_s": None, "model": None}
                continue
            try:
                hb = json.loads(hb_raw)
            except Exception:
                out[aid] = {"alive": False, "status": "heartbeat_parse_error", "age_s": None, "model": None}
                continue
            age = now - int(hb.get("ts", 0))
            if age < 180:
                status = hb.get("status", "online") or "online"
                out[aid] = {"alive": True, "status": status, "age_s": age, "model": hb.get("model")}
            elif age < 600:
                out[aid] = {"alive": True, "status": "stale", "age_s": age, "model": hb.get("model")}
            else:
                out[aid] = {"alive": False, "status": "offline", "age_s": age, "model": hb.get("model")}
    except Exception as e:
        for a in agent_ids:
            out[a] = {"alive": None, "status": f"redis_error:{e}", "age_s": None, "model": None}
    return out

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
hours = int(args.get("hours", 4))
agent = args.get("agent", "")
kind = args.get("kind", "")
limit = int(args.get("limit", 60))
fmt = args.get("format", "summary")

DB_CONFIG = {
    "host": os.environ.get("BAZA_DB_HOST", "100.127.118.103"),
    "port": int(os.environ.get("BAZA_DB_PORT", "5432")),
    "dbname": os.environ.get("BAZA_DB_NAME", "baza_agents"),
    "user": os.environ.get("BAZA_DB_USER", "switchhacker"),
    "password": os.environ.get("DB_PASSWORD", "baza2026"),
}

AGENT_ICONS = {
    "simon_bately": "🎯", "claw_batto": "🔧", "phil_hass": "⚖️",
    "sam_axe": "🎨", "rex_valor": "📞", "duke_harmon": "📋",
    "scout_reeves": "🔍", "nova_sterling": "💬", "specter_voss": "👻",
}


def fmt_agent(aid):
    icon = AGENT_ICONS.get(aid, "•")
    short = aid.split("_")[0].title() if aid else "?"
    return f"{icon} {short}"


try:
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    # Build filtered query
    where = ["ts > NOW() - INTERVAL %s"]
    params = [f"{hours} hours"]
    if agent:
        where.append("agent_id = %s")
        params.append(agent)
    if kind:
        where.append("kind = %s")
        params.append(kind)

    q = f"""
        SELECT ts, agent_id, kind, subkind, summary, success
        FROM team_activity
        WHERE {' AND '.join(where)}
        ORDER BY ts DESC
        LIMIT %s
    """
    params.append(limit)
    cur.execute(q, params)
    rows = cur.fetchall()

    # Also get per-agent counts
    cur.execute(f"""
        SELECT agent_id, kind, count(*)
        FROM team_activity
        WHERE {' AND '.join(where[:1])}
        GROUP BY agent_id, kind
        ORDER BY agent_id
    """, [f"{hours} hours"])
    counts = cur.fetchall()

    cur.close()
    conn.close()
except Exception as e:
    print(f"DB error: {e}")
    sys.exit(1)

# ── Output ────────────────────────────────────────────────────────────────────
print(f"=== TEAM PULSE — last {hours}h ===")
print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

# Activity summary per agent
print("ACTIVITY BY AGENT:")
agent_totals = {}
for aid, k, c in counts:
    if aid not in agent_totals:
        agent_totals[aid] = {"chat": 0, "action": 0}
    agent_totals[aid][k] = c

for aid in sorted(agent_totals.keys()):
    t = agent_totals[aid]
    total = t.get("chat", 0) + t.get("action", 0)
    bar = "█" * min(20, total)
    print(f"  {fmt_agent(aid):18} chat={t.get('chat',0):3} action={t.get('action',0):3}  {bar}")

all_agents = ["simon_bately", "claw_batto", "phil_hass", "sam_axe",
              "rex_valor", "duke_harmon", "scout_reeves", "nova_sterling", "specter_voss"]

# ── CRITICAL: check Redis heartbeats for REAL alive/dead state ────────────
# "no activity in N hours" does NOT mean the agent is down — it might just be idle.
# The only authoritative signal for "process running" is the Redis heartbeat.
hb = _check_heartbeats(all_agents)

print("\nPROCESS HEALTH (from Redis heartbeats):")
alive_count = 0
dead_count = 0
idle_count = 0
for aid in all_agents:
    h = hb.get(aid, {})
    if h.get("alive") is True:
        alive_count += 1
        actions = agent_totals.get(aid, {}).get("action", 0) + agent_totals.get(aid, {}).get("chat", 0)
        if actions == 0:
            idle_count += 1
            tag = f"🟢 ALIVE · IDLE (no work in {hours}h — not broken, just waiting)"
        else:
            tag = f"🟢 ALIVE · WORKING ({actions} actions/chats in {hours}h)"
        age = h.get("age_s")
        age_str = f"{age}s ago" if age is not None else "?"
        model = h.get("model") or "?"
        print(f"  {fmt_agent(aid):18} {tag}   last heartbeat {age_str}   [{model}]")
    elif h.get("alive") is False:
        dead_count += 1
        status = h.get("status", "offline")
        print(f"  {fmt_agent(aid):18} 🔴 DOWN · {status}")
    else:
        print(f"  {fmt_agent(aid):18} ❓ UNKNOWN · {h.get('status','heartbeat_check_failed')}")

print(f"\nSUMMARY: {alive_count} alive ({idle_count} idle, {alive_count - idle_count} working), {dead_count} down")
if dead_count == 0:
    print("✅ All agents are running. 'Silent' agents are idle, not broken. DO NOT propose systemd restarts.")
else:
    print(f"⚠️  {dead_count} agent(s) actually down — these need investigation BEFORE any restart.")

# Legacy "silent" warning kept for informational purposes only (no longer triggers action)
silent_info = set(all_agents) - set(agent_totals.keys())
if silent_info:
    print(f"\nℹ️  No team_activity rows in {hours}h for: {', '.join(sorted(silent_info))}")
    print("   (This is INFORMATIONAL only — check PROCESS HEALTH above for real status.)")

print(f"\n=== RECENT EVENTS ({len(rows)}) ===")
for ts, aid, k, subk, summary, success in rows:
    when = ts.strftime("%H:%M:%S")
    marker = "✓" if success is None or success else "✗"
    if fmt == "detail":
        print(f"[{when}] {fmt_agent(aid)} {k}/{subk} {marker}")
        print(f"   {summary[:200]}")
    else:
        print(f"[{when}] {fmt_agent(aid):18} {k:6} {subk:25.25} {marker} {summary[:80]}")

print(f"\n=== END PULSE ===")
