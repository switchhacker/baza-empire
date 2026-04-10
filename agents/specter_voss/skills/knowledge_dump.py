#!/usr/bin/env python3
"""
Specter Voss — Empire Knowledge Export
Reads empire_knowledge, agent_memory, and agent_summaries tables.
Produces a structured intelligence report.

Args:
    {"agent": "claw_batto", "category": "skills"}
    Both optional — omit for full dump.
"""
import os, json
from datetime import datetime

SKILL_ARGS = json.loads(os.environ.get("SKILL_ARGS", "{}"))

DB_CONFIG = {
    "host": os.environ.get("BAZA_DB_HOST", "localhost"),
    "port": int(os.environ.get("BAZA_DB_PORT", "5432")),
    "dbname": os.environ.get("BAZA_DB_NAME", "baza_agents"),
    "user": os.environ.get("BAZA_DB_USER", "switchhacker"),
    "password": os.environ.get("DB_PASSWORD", "baza2026"),
}


def get_pg():
    """Return a PostgreSQL connection or None."""
    try:
        import psycopg2
        return psycopg2.connect(**DB_CONFIG)
    except Exception as e:
        print(f"ERROR: Cannot connect to PostgreSQL: {e}")
        return None


def dump_empire_knowledge(conn):
    """Read all empire_knowledge rows."""
    try:
        cur = conn.cursor()
        cur.execute("SELECT key, value, category, created_at FROM empire_knowledge ORDER BY category, key")
        rows = cur.fetchall()
        cur.close()
        return rows
    except Exception as e:
        return [("_error", str(e), "", None)]


def dump_agent_memory(conn, agent_id=None, category=None):
    """Read agent_memory rows, optionally filtered."""
    try:
        cur = conn.cursor()
        query = "SELECT agent_id, key, value, category FROM agent_memory WHERE 1=1"
        params = []
        if agent_id:
            query += " AND agent_id = %s"
            params.append(agent_id)
        if category:
            query += " AND category = %s"
            params.append(category)
        query += " ORDER BY agent_id, category, key"
        cur.execute(query, params)
        rows = cur.fetchall()
        cur.close()
        return rows
    except Exception as e:
        return [("_error", str(e), "", "")]


def dump_agent_summaries(conn, agent_id=None, limit=20):
    """Read recent agent_summaries."""
    try:
        cur = conn.cursor()
        if agent_id:
            cur.execute(
                "SELECT agent_id, summary, created_at FROM agent_summaries "
                "WHERE agent_id = %s ORDER BY created_at DESC LIMIT %s",
                (agent_id, limit),
            )
        else:
            cur.execute(
                "SELECT agent_id, summary, created_at FROM agent_summaries "
                "ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
        rows = cur.fetchall()
        cur.close()
        return rows
    except Exception as e:
        return [("_error", str(e), None)]


def main():
    target_agent = SKILL_ARGS.get("agent")
    target_category = SKILL_ARGS.get("category")

    conn = get_pg()
    if not conn:
        return

    print("=== EMPIRE KNOWLEDGE DUMP ===")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if target_agent:
        print(f"Filter — Agent: {target_agent}")
    if target_category:
        print(f"Filter — Category: {target_category}")
    print()

    # Section 1: Empire Knowledge (shared facts)
    print("[EMPIRE KNOWLEDGE] (shared across all agents)")
    ek_rows = dump_empire_knowledge(conn)
    if ek_rows and ek_rows[0][0] != "_error":
        by_cat = {}
        for key, value, cat, ts in ek_rows:
            cat = cat or "general"
            by_cat.setdefault(cat, []).append((key, value, ts))

        for cat in sorted(by_cat):
            print(f"\n  [{cat}] ({len(by_cat[cat])} entries)")
            for key, value, ts in by_cat[cat]:
                val_preview = (str(value) or "")[:120]
                ts_str = ts.strftime("%m-%d") if ts else ""
                print(f"    {key}: {val_preview}" + (f" ({ts_str})" if ts_str else ""))
        print(f"\n  Total: {len(ek_rows)} entries")
    elif ek_rows and ek_rows[0][0] == "_error":
        print(f"  Error: {ek_rows[0][1]}")
    else:
        print("  (empty)")
    print()

    # Section 2: Agent Memory
    print("[AGENT MEMORY]")
    am_rows = dump_agent_memory(conn, target_agent, target_category)
    if am_rows and am_rows[0][0] != "_error":
        by_agent = {}
        for agent_id, key, value, cat in am_rows:
            by_agent.setdefault(agent_id, []).append((key, value, cat))

        for agent_id in sorted(by_agent):
            entries = by_agent[agent_id]
            print(f"\n  --- {agent_id} ({len(entries)} memories) ---")

            # Group by category within agent
            by_cat = {}
            for key, value, cat in entries:
                cat = cat or "uncategorized"
                by_cat.setdefault(cat, []).append((key, value))

            for cat in sorted(by_cat):
                items = by_cat[cat]
                print(f"    [{cat}] ({len(items)} items)")
                for key, value in items[:10]:
                    val_preview = (str(value) or "")[:100]
                    print(f"      {key}: {val_preview}")
                if len(items) > 10:
                    print(f"      ... +{len(items) - 10} more")

        print(f"\n  Total: {len(am_rows)} entries across {len(by_agent)} agents")
    elif am_rows and am_rows[0][0] == "_error":
        print(f"  Error: {am_rows[0][1]}")
    else:
        print("  (empty)")
    print()

    # Section 3: Agent Summaries
    limit = 5 if target_agent else 20
    print(f"[AGENT SUMMARIES] (last {limit})")
    summaries = dump_agent_summaries(conn, target_agent, limit)
    if summaries and summaries[0][0] != "_error":
        for agent_id, summary, ts in summaries:
            ts_str = ts.strftime("%Y-%m-%d %H:%M") if ts else "?"
            summary_preview = (summary or "")[:200]
            print(f"  [{ts_str}] {agent_id}:")
            print(f"    {summary_preview}")
            if len(summary or "") > 200:
                print(f"    ... ({len(summary)} chars total)")
        print(f"\n  Total shown: {len(summaries)}")
    elif summaries and summaries[0][0] == "_error":
        print(f"  Error: {summaries[0][1]}")
    else:
        print("  (empty)")

    conn.close()
    print()
    print("=== DUMP COMPLETE ===")


if __name__ == "__main__":
    main()
