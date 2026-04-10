#!/usr/bin/env python3
"""
Specter Voss — Agent Pulse
Shows all agents' status: heartbeats, memory counts, recent journal activity.
Detects who's online, idle, or erroring.

Args:
    {"agent": "specific_agent_id"}  — deep dive on one agent
"""
import os, json
from datetime import datetime, timedelta

SKILL_ARGS = json.loads(os.environ.get("SKILL_ARGS", "{}"))

DB_CONFIG = {
    "host": os.environ.get("BAZA_DB_HOST", "localhost"),
    "port": int(os.environ.get("BAZA_DB_PORT", "5432")),
    "dbname": os.environ.get("BAZA_DB_NAME", "baza_agents"),
    "user": os.environ.get("BAZA_DB_USER", "switchhacker"),
    "password": os.environ.get("DB_PASSWORD", "baza2026"),
}

KNOWN_AGENTS = [
    "simon_bately", "claw_batto", "phil_hass", "sam_axe",
    "rex_maul", "duke_silver", "scout_reeves", "nova_chen",
    "specter_voss",
]


def get_redis():
    """Return a Redis connection or None."""
    try:
        import redis
        return redis.Redis(
            host=os.environ.get("BAZA_REDIS_HOST", "localhost"),
            port=int(os.environ.get("BAZA_REDIS_PORT", "6379")),
            decode_responses=True,
        )
    except Exception:
        return None


def get_pg():
    """Return a PostgreSQL connection or None."""
    try:
        import psycopg2
        return psycopg2.connect(**DB_CONFIG)
    except Exception:
        return None


def get_heartbeats(r):
    """Read all baza:heartbeat:* keys from Redis."""
    heartbeats = {}
    if not r:
        return heartbeats
    try:
        for key in r.keys("baza:heartbeat:*"):
            agent_id = key.split(":")[-1]
            data = r.get(key)
            if data:
                try:
                    heartbeats[agent_id] = json.loads(data)
                except json.JSONDecodeError:
                    heartbeats[agent_id] = {"raw": data}
    except Exception as e:
        heartbeats["_error"] = str(e)
    return heartbeats


def get_memory_counts(conn):
    """Get agent_memory row counts per agent."""
    counts = {}
    if not conn:
        return counts
    try:
        cur = conn.cursor()
        cur.execute("SELECT agent_id, COUNT(*) FROM agent_memory GROUP BY agent_id ORDER BY agent_id")
        for row in cur.fetchall():
            counts[row[0]] = row[1]
        cur.close()
    except Exception as e:
        counts["_error"] = str(e)
    return counts


def get_recent_journal(conn, agent_id=None, limit=20):
    """Get recent task_journal entries, optionally filtered by agent."""
    entries = []
    if not conn:
        return entries
    try:
        cur = conn.cursor()
        if agent_id:
            cur.execute(
                "SELECT agent_id, action, detail, created_at FROM task_journal "
                "WHERE agent_id = %s ORDER BY created_at DESC LIMIT %s",
                (agent_id, limit),
            )
        else:
            cur.execute(
                "SELECT agent_id, action, detail, created_at FROM task_journal "
                "ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
        for row in cur.fetchall():
            entries.append({
                "agent": row[0],
                "action": row[1],
                "detail": (row[2] or "")[:120],
                "time": row[3].strftime("%Y-%m-%d %H:%M") if row[3] else "?",
            })
        cur.close()
    except Exception as e:
        entries.append({"error": str(e)})
    return entries


def get_agent_deep_dive(conn, r, agent_id):
    """Deep dive into a single agent."""
    print(f"\n=== AGENT DEEP DIVE: {agent_id} ===\n")

    # Heartbeat
    hb = get_heartbeats(r).get(agent_id)
    if hb:
        print(f"[HEARTBEAT]")
        for k, v in (hb if isinstance(hb, dict) else {"value": hb}).items():
            print(f"  {k}: {v}")
    else:
        print("[HEARTBEAT] No heartbeat found")

    # Memory
    if conn:
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT key, value, category FROM agent_memory WHERE agent_id = %s ORDER BY category, key",
                (agent_id,),
            )
            rows = cur.fetchall()
            print(f"\n[MEMORY] {len(rows)} entries")
            by_cat = {}
            for key, value, cat in rows:
                cat = cat or "uncategorized"
                by_cat.setdefault(cat, []).append((key, value))
            for cat in sorted(by_cat):
                print(f"  [{cat}] ({len(by_cat[cat])} items)")
                for key, value in by_cat[cat][:5]:
                    val_preview = (str(value) or "")[:80]
                    print(f"    {key}: {val_preview}")
                if len(by_cat[cat]) > 5:
                    print(f"    ... +{len(by_cat[cat]) - 5} more")
            cur.close()
        except Exception as e:
            print(f"\n[MEMORY] Error: {e}")

        # Recent summaries
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT summary, created_at FROM agent_summaries WHERE agent_id = %s "
                "ORDER BY created_at DESC LIMIT 3",
                (agent_id,),
            )
            rows = cur.fetchall()
            print(f"\n[RECENT SUMMARIES] {len(rows)} found")
            for summary, ts in rows:
                ts_str = ts.strftime("%Y-%m-%d %H:%M") if ts else "?"
                print(f"  [{ts_str}] {(summary or '')[:150]}")
            cur.close()
        except Exception as e:
            print(f"\n[SUMMARIES] Error: {e}")

    # Journal
    journal = get_recent_journal(conn, agent_id, limit=10)
    print(f"\n[RECENT JOURNAL] {len(journal)} entries")
    for e in journal:
        if "error" in e:
            print(f"  Error: {e['error']}")
        else:
            print(f"  [{e['time']}] {e['action']}: {e['detail']}")


def main():
    target_agent = SKILL_ARGS.get("agent")

    r = get_redis()
    conn = get_pg()

    if target_agent:
        get_agent_deep_dive(conn, r, target_agent)
        if conn:
            conn.close()
        return

    # Overview of all agents
    print("=== AGENT PULSE — ALL AGENTS ===")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    heartbeats = get_heartbeats(r)
    memory_counts = get_memory_counts(conn)
    journal = get_recent_journal(conn, limit=50)

    # Build per-agent journal counts (last 24h activity)
    journal_activity = {}
    for e in journal:
        agent = e.get("agent", "?")
        journal_activity[agent] = journal_activity.get(agent, 0) + 1

    # Determine status for each agent
    now = datetime.now()
    print(f"{'Agent':<18} {'Status':<10} {'Heartbeat':<22} {'Memory':<8} {'Journal':<8}")
    print("-" * 70)

    all_agents = sorted(set(KNOWN_AGENTS) | set(heartbeats.keys()) | set(memory_counts.keys()))
    for agent_id in all_agents:
        hb = heartbeats.get(agent_id)
        mem = memory_counts.get(agent_id, 0)
        jcount = journal_activity.get(agent_id, 0)

        # Determine status from heartbeat
        status = "UNKNOWN"
        hb_time = "—"
        if hb and isinstance(hb, dict):
            hb_time = hb.get("timestamp", hb.get("time", "?"))
            hb_status = hb.get("status", "")
            if hb_status == "error" or "error" in str(hb).lower():
                status = "ERROR"
            elif hb_status in ("active", "online", "running"):
                status = "ONLINE"
            else:
                status = "IDLE"
            # Try to parse time to detect stale heartbeats
            try:
                ts = datetime.fromisoformat(str(hb_time).replace("Z", "+00:00").replace("+00:00", ""))
                if (now - ts) > timedelta(minutes=30):
                    status = "STALE"
            except Exception:
                pass
        elif hb:
            hb_time = str(hb)[:20]
            status = "SEEN"
        else:
            status = "OFFLINE"

        print(f"  {agent_id:<16} {status:<10} {str(hb_time):<22} {mem:<8} {jcount:<8}")

    # Recent journal entries
    print(f"\n[RECENT ACTIVITY] (last {len(journal)} journal entries)")
    for e in journal[:15]:
        if "error" in e:
            print(f"  Error: {e['error']}")
        else:
            print(f"  [{e['time']}] {e['agent']}: {e['action']} — {e['detail']}")

    if conn:
        conn.close()


if __name__ == "__main__":
    main()
