"""
Baza Empire — Agent Context DB
--------------------------------
Persistent memory + skills registry for all agents.
Each agent gets:
  - Long-term memory (key/value facts)
  - Compressed conversation summaries
  - Shared empire-wide knowledge
  - Skills registry (what tools/skills each agent can run)
  - Task journal (what was done, when, outcome)
"""

import psycopg2
import psycopg2.extras
import psycopg2.pool
import os
import json
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": "baza_agents",
    "user": "switchhacker",
    "password": os.environ.get("DB_PASSWORD", "baza2026")
}

# ── Connection pool ────────────────────────────────────────────────────────────
# min=2 always-warm connections, max=12 for 8 concurrent agents + headroom
_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None or _pool.closed:
        _pool = psycopg2.pool.ThreadedConnectionPool(minconn=2, maxconn=12, **DB_CONFIG)
    return _pool


def get_conn():
    """Get a connection from the pool. Caller must call release_conn(conn) when done."""
    return _get_pool().getconn()


def release_conn(conn):
    """Return a connection to the pool."""
    try:
        _get_pool().putconn(conn)
    except Exception:
        pass


class _DB:
    """Context manager: borrows a connection from the pool, returns it on exit."""
    def __enter__(self):
        self.conn = get_conn()
        self.cur = self.conn.cursor()
        return self.cur, self.conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.conn.rollback()
        else:
            self.conn.commit()
        self.cur.close()
        release_conn(self.conn)
        return False


def init_context_db():
    """Create all context tables if they don't exist."""
    with _DB() as (cur, conn):
        cur.execute("""
            -- Per-agent long-term memory (facts, preferences, state)
            CREATE TABLE IF NOT EXISTS agent_memory (
                id SERIAL PRIMARY KEY,
                agent_id VARCHAR(50) NOT NULL,
                key VARCHAR(200) NOT NULL,
                value TEXT,
                category VARCHAR(50) DEFAULT 'general',
                updated_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(agent_id, key)
            );

            -- Compressed conversation summaries (like Brad's memory.md)
            CREATE TABLE IF NOT EXISTS agent_summaries (
                id SERIAL PRIMARY KEY,
                agent_id VARCHAR(50) NOT NULL,
                chat_id BIGINT,
                summary TEXT NOT NULL,
                session_date TIMESTAMP DEFAULT NOW(),
                message_count INT DEFAULT 0
            );

            -- Shared empire-wide knowledge (all agents can read)
            CREATE TABLE IF NOT EXISTS empire_knowledge (
                id SERIAL PRIMARY KEY,
                key VARCHAR(200) NOT NULL UNIQUE,
                value TEXT,
                category VARCHAR(50) DEFAULT 'general',
                updated_at TIMESTAMP DEFAULT NOW(),
                updated_by VARCHAR(50)
            );

            -- Skills registry (what skills each agent has available)
            CREATE TABLE IF NOT EXISTS agent_skills (
                id SERIAL PRIMARY KEY,
                agent_id VARCHAR(50) NOT NULL,
                skill_name VARCHAR(100) NOT NULL,
                description TEXT,
                script_path VARCHAR(300),
                parameters JSONB DEFAULT '{}',
                last_run TIMESTAMP,
                run_count INT DEFAULT 0,
                UNIQUE(agent_id, skill_name)
            );

            -- Task journal (log of everything agents have done)
            CREATE TABLE IF NOT EXISTS task_journal (
                id SERIAL PRIMARY KEY,
                agent_id VARCHAR(50) NOT NULL,
                chat_id BIGINT,
                task_type VARCHAR(100),
                task_description TEXT,
                input_data JSONB DEFAULT '{}',
                result TEXT,
                success BOOLEAN DEFAULT TRUE,
                duration_ms INT,
                created_at TIMESTAMP DEFAULT NOW()
            );

            -- Agent identity/soul (persona, role, rules — editable at runtime)
            CREATE TABLE IF NOT EXISTS agent_identity (
                agent_id VARCHAR(50) PRIMARY KEY,
                name VARCHAR(100),
                role TEXT,
                soul TEXT,
                system_prompt TEXT,
                updated_at TIMESTAMP DEFAULT NOW()
            );
        """)
    logger.info("Context DB initialized.")


# ── Memory ────────────────────────────────────────────────────────────────────

def memory_set(agent_id: str, key: str, value: str, category: str = "general"):
    """Store or update a memory fact for an agent."""
    with _DB() as (cur, _):
        cur.execute("""
            INSERT INTO agent_memory (agent_id, key, value, category, updated_at)
            VALUES (%s, %s, %s, %s, NOW())
            ON CONFLICT (agent_id, key) DO UPDATE
            SET value = EXCLUDED.value, category = EXCLUDED.category, updated_at = NOW()
        """, (agent_id, key, value, category))


def memory_get(agent_id: str, key: str) -> Optional[str]:
    """Get a specific memory fact."""
    with _DB() as (cur, _):
        cur.execute(
            "SELECT value FROM agent_memory WHERE agent_id = %s AND key = %s",
            (agent_id, key)
        )
        row = cur.fetchone()
    return row[0] if row else None


def memory_get_all(agent_id: str, category: str = None, limit: int = 20) -> dict:
    """
    Get memory facts for an agent. Capped at `limit` most recently updated.
    Optionally filter by category.
    """
    with _DB() as (cur, _):
        if category:
            cur.execute(
                """SELECT key, value, category FROM agent_memory
                   WHERE agent_id = %s AND category = %s
                   ORDER BY updated_at DESC LIMIT %s""",
                (agent_id, category, limit)
            )
        else:
            cur.execute(
                """SELECT key, value, category FROM agent_memory
                   WHERE agent_id = %s
                   ORDER BY updated_at DESC LIMIT %s""",
                (agent_id, limit)
            )
        rows = cur.fetchall()
    return {r[0]: {"value": r[1], "category": r[2]} for r in rows}


def memory_delete(agent_id: str, key: str):
    with _DB() as (cur, _):
        cur.execute("DELETE FROM agent_memory WHERE agent_id = %s AND key = %s", (agent_id, key))


# ── Summaries ─────────────────────────────────────────────────────────────────

def save_summary(agent_id: str, summary: str, chat_id: int = None, message_count: int = 0):
    """Save a compressed conversation summary."""
    with _DB() as (cur, _):
        cur.execute("""
            INSERT INTO agent_summaries (agent_id, chat_id, summary, message_count)
            VALUES (%s, %s, %s, %s)
        """, (agent_id, chat_id, summary, message_count))


def get_summaries(agent_id: str, limit: int = 3) -> list:
    """Get recent conversation summaries for context."""
    with _DB() as (cur, _):
        cur.execute("""
            SELECT summary, session_date, message_count
            FROM agent_summaries
            WHERE agent_id = %s
            ORDER BY session_date DESC LIMIT %s
        """, (agent_id, limit))
        rows = cur.fetchall()
    return [{"summary": r[0], "date": r[1].strftime("%Y-%m-%d %H:%M"), "messages": r[2]} for r in reversed(rows)]


# ── Empire Knowledge ──────────────────────────────────────────────────────────

def empire_set(key: str, value: str, category: str = "general", updated_by: str = "system"):
    with _DB() as (cur, _):
        cur.execute("""
            INSERT INTO empire_knowledge (key, value, category, updated_at, updated_by)
            VALUES (%s, %s, %s, NOW(), %s)
            ON CONFLICT (key) DO UPDATE
            SET value = EXCLUDED.value, category = EXCLUDED.category,
                updated_at = NOW(), updated_by = EXCLUDED.updated_by
        """, (key, value, category, updated_by))


def empire_get(key: str) -> Optional[str]:
    with _DB() as (cur, _):
        cur.execute("SELECT value FROM empire_knowledge WHERE key = %s", (key,))
        row = cur.fetchone()
    return row[0] if row else None


def empire_get_category(category: str) -> dict:
    with _DB() as (cur, _):
        cur.execute(
            "SELECT key, value FROM empire_knowledge WHERE category = %s ORDER BY key",
            (category,)
        )
        rows = cur.fetchall()
    return {r[0]: r[1] for r in rows}


def empire_get_all(limit_per_category: int = 10) -> dict:
    """Return empire knowledge grouped by category, capped per category."""
    with _DB() as (cur, _):
        # Use window function to cap per-category
        cur.execute("""
            SELECT key, value, category FROM (
                SELECT key, value, category,
                       ROW_NUMBER() OVER (PARTITION BY category ORDER BY updated_at DESC) as rn
                FROM empire_knowledge
            ) ranked WHERE rn <= %s
            ORDER BY category, key
        """, (limit_per_category,))
        rows = cur.fetchall()
    result = {}
    for r in rows:
        if r[2] not in result:
            result[r[2]] = {}
        result[r[2]][r[0]] = r[1]
    return result


# ── Skills Registry ───────────────────────────────────────────────────────────

def register_skill(agent_id: str, skill_name: str, description: str,
                   script_path: str, parameters: dict = {}):
    """Register a skill for an agent."""
    with _DB() as (cur, _):
        cur.execute("""
            INSERT INTO agent_skills (agent_id, skill_name, description, script_path, parameters)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (agent_id, skill_name) DO UPDATE
            SET description = EXCLUDED.description, script_path = EXCLUDED.script_path,
                parameters = EXCLUDED.parameters
        """, (agent_id, skill_name, description, script_path, json.dumps(parameters)))


def get_skills(agent_id: str) -> list:
    """Get all skills registered for an agent."""
    with _DB() as (cur, _):
        cur.execute("""
            SELECT skill_name, description, script_path, parameters, last_run, run_count
            FROM agent_skills WHERE agent_id = %s ORDER BY skill_name
        """, (agent_id,))
        rows = cur.fetchall()
    return [{
        "name": r[0],
        "description": r[1],
        "script_path": r[2],
        "parameters": r[3],
        "last_run": r[4].strftime("%Y-%m-%d %H:%M") if r[4] else None,
        "run_count": r[5]
    } for r in rows]


def skill_ran(agent_id: str, skill_name: str):
    """Update last_run and increment run_count for a skill."""
    with _DB() as (cur, _):
        cur.execute("""
            UPDATE agent_skills
            SET last_run = NOW(), run_count = run_count + 1
            WHERE agent_id = %s AND skill_name = %s
        """, (agent_id, skill_name))


# ── Task Journal ──────────────────────────────────────────────────────────────

def journal_log(agent_id: str, task_type: str, task_description: str,
                result: str = None, success: bool = True,
                input_data: dict = {}, duration_ms: int = None,
                chat_id: int = None):
    with _DB() as (cur, _):
        cur.execute("""
            INSERT INTO task_journal
            (agent_id, chat_id, task_type, task_description, input_data, result, success, duration_ms)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (agent_id, chat_id, task_type, task_description,
              json.dumps(input_data), result, success, duration_ms))


def journal_get(agent_id: str, limit: int = 20, task_type: str = None) -> list:
    with _DB() as (cur, _):
        if task_type:
            cur.execute("""
                SELECT task_type, task_description, result, success, created_at
                FROM task_journal WHERE agent_id = %s AND task_type = %s
                ORDER BY created_at DESC LIMIT %s
            """, (agent_id, task_type, limit))
        else:
            cur.execute("""
                SELECT task_type, task_description, result, success, created_at
                FROM task_journal WHERE agent_id = %s
                ORDER BY created_at DESC LIMIT %s
            """, (agent_id, limit))
        rows = cur.fetchall()
    return [{
        "type": r[0], "description": r[1], "result": r[2],
        "success": r[3], "date": r[4].strftime("%Y-%m-%d %H:%M")
    } for r in rows]


# ── Agent Identity ────────────────────────────────────────────────────────────

def identity_set(agent_id: str, name: str, role: str, soul: str, system_prompt: str):
    with _DB() as (cur, _):
        cur.execute("""
            INSERT INTO agent_identity (agent_id, name, role, soul, system_prompt, updated_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
            ON CONFLICT (agent_id) DO UPDATE
            SET name = EXCLUDED.name, role = EXCLUDED.role, soul = EXCLUDED.soul,
                system_prompt = EXCLUDED.system_prompt, updated_at = NOW()
        """, (agent_id, name, role, soul, system_prompt))


def identity_get(agent_id: str) -> Optional[dict]:
    with _DB() as (cur, _):
        cur.execute(
            "SELECT name, role, soul, system_prompt FROM agent_identity WHERE agent_id = %s",
            (agent_id,)
        )
        row = cur.fetchone()
    return {"name": row[0], "role": row[1], "soul": row[2], "system_prompt": row[3]} if row else None


# ── Context Builder ───────────────────────────────────────────────────────────

# Char limits for each section to keep total context tight
_MEMORY_CHAR_LIMIT    = 800
_EMPIRE_CHAR_LIMIT    = 600
_SUMMARY_CHAR_LIMIT   = 500
_TOTAL_CHAR_LIMIT     = 2200


def build_agent_context(agent_id: str) -> str:
    """
    Build a compact context string for an agent's system prompt injection.
    Includes: recent memory, empire knowledge, and recent summaries.
    Hard-capped at ~2200 chars to stay well inside model context windows.

    NOTE: Identity/system_prompt and skills are injected separately.
    """
    sections = []

    # Memory — most recently updated facts only (already limited in query)
    memories = memory_get_all(agent_id, limit=20)
    if memories:
        mem_lines = []
        chars = 0
        for key, data in memories.items():
            line = f"- [{data['category']}] {key}: {data['value']}"
            chars += len(line)
            if chars > _MEMORY_CHAR_LIMIT:
                break
            mem_lines.append(line)
        if mem_lines:
            sections.append("## Memory\n" + "\n".join(mem_lines))

    # Empire Knowledge — capped per-category in the query, trim to char limit
    empire = empire_get_all(limit_per_category=5)
    if empire:
        emp_lines = []
        chars = 0
        for category, items in empire.items():
            emp_lines.append(f"### {category.upper()}")
            for k, v in items.items():
                line = f"  - {k}: {v}"
                chars += len(line)
                if chars > _EMPIRE_CHAR_LIMIT:
                    break
                emp_lines.append(line)
            if chars > _EMPIRE_CHAR_LIMIT:
                break
        if emp_lines:
            sections.append("## Empire Knowledge\n" + "\n".join(emp_lines))

    # Summaries — last 2 only
    summaries = get_summaries(agent_id, limit=2)
    if summaries:
        sum_lines = []
        chars = 0
        for s in summaries:
            line = f"- [{s['date']}] {s['summary']}"
            chars += len(line)
            if chars > _SUMMARY_CHAR_LIMIT:
                break
            sum_lines.append(line)
        if sum_lines:
            sections.append("## Recent Sessions\n" + "\n".join(sum_lines))

    result = "\n\n---\n\n".join(sections)
    # Final hard cap
    if len(result) > _TOTAL_CHAR_LIMIT:
        result = result[:_TOTAL_CHAR_LIMIT] + "\n...[context trimmed]"
    return result
