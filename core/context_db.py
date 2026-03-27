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


def get_conn():
    return psycopg2.connect(**DB_CONFIG)


def init_context_db():
    """Create all context tables if they don't exist."""
    conn = get_conn()
    cur = conn.cursor()

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

    conn.commit()
    cur.close()
    conn.close()
    logger.info("Context DB initialized.")


# ── Memory ────────────────────────────────────────────────────────────────────

def memory_set(agent_id: str, key: str, value: str, category: str = "general"):
    """Store or update a memory fact for an agent."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO agent_memory (agent_id, key, value, category, updated_at)
        VALUES (%s, %s, %s, %s, NOW())
        ON CONFLICT (agent_id, key) DO UPDATE
        SET value = EXCLUDED.value, category = EXCLUDED.category, updated_at = NOW()
    """, (agent_id, key, value, category))
    conn.commit()
    cur.close()
    conn.close()


def memory_get(agent_id: str, key: str) -> Optional[str]:
    """Get a specific memory fact."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT value FROM agent_memory WHERE agent_id = %s AND key = %s",
        (agent_id, key)
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else None


def memory_get_all(agent_id: str, category: str = None) -> dict:
    """Get all memory facts for an agent, optionally filtered by category."""
    conn = get_conn()
    cur = conn.cursor()
    if category:
        cur.execute(
            "SELECT key, value, category FROM agent_memory WHERE agent_id = %s AND category = %s ORDER BY key",
            (agent_id, category)
        )
    else:
        cur.execute(
            "SELECT key, value, category FROM agent_memory WHERE agent_id = %s ORDER BY category, key",
            (agent_id,)
        )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {r[0]: {"value": r[1], "category": r[2]} for r in rows}


def memory_delete(agent_id: str, key: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM agent_memory WHERE agent_id = %s AND key = %s", (agent_id, key))
    conn.commit()
    cur.close()
    conn.close()


# ── Summaries ─────────────────────────────────────────────────────────────────

def save_summary(agent_id: str, summary: str, chat_id: int = None, message_count: int = 0):
    """Save a compressed conversation summary."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO agent_summaries (agent_id, chat_id, summary, message_count)
        VALUES (%s, %s, %s, %s)
    """, (agent_id, chat_id, summary, message_count))
    conn.commit()
    cur.close()
    conn.close()


def get_summaries(agent_id: str, limit: int = 5) -> list:
    """Get recent conversation summaries for context."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT summary, session_date, message_count
        FROM agent_summaries
        WHERE agent_id = %s
        ORDER BY session_date DESC LIMIT %s
    """, (agent_id, limit))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [{"summary": r[0], "date": r[1].strftime("%Y-%m-%d %H:%M"), "messages": r[2]} for r in reversed(rows)]


# ── Empire Knowledge ──────────────────────────────────────────────────────────

def empire_set(key: str, value: str, category: str = "general", updated_by: str = "system"):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO empire_knowledge (key, value, category, updated_at, updated_by)
        VALUES (%s, %s, %s, NOW(), %s)
        ON CONFLICT (key) DO UPDATE
        SET value = EXCLUDED.value, category = EXCLUDED.category,
            updated_at = NOW(), updated_by = EXCLUDED.updated_by
    """, (key, value, category, updated_by))
    conn.commit()
    cur.close()
    conn.close()


def empire_get(key: str) -> Optional[str]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT value FROM empire_knowledge WHERE key = %s", (key,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else None


def empire_get_category(category: str) -> dict:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT key, value FROM empire_knowledge WHERE category = %s ORDER BY key",
        (category,)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {r[0]: r[1] for r in rows}


def empire_get_all() -> dict:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT key, value, category FROM empire_knowledge ORDER BY category, key")
    rows = cur.fetchall()
    cur.close()
    conn.close()
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
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO agent_skills (agent_id, skill_name, description, script_path, parameters)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (agent_id, skill_name) DO UPDATE
        SET description = EXCLUDED.description, script_path = EXCLUDED.script_path,
            parameters = EXCLUDED.parameters
    """, (agent_id, skill_name, description, script_path, json.dumps(parameters)))
    conn.commit()
    cur.close()
    conn.close()


def get_skills(agent_id: str) -> list:
    """Get all skills registered for an agent."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT skill_name, description, script_path, parameters, last_run, run_count
        FROM agent_skills WHERE agent_id = %s ORDER BY skill_name
    """, (agent_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
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
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        UPDATE agent_skills
        SET last_run = NOW(), run_count = run_count + 1
        WHERE agent_id = %s AND skill_name = %s
    """, (agent_id, skill_name))
    conn.commit()
    cur.close()
    conn.close()


# ── Task Journal ──────────────────────────────────────────────────────────────

def journal_log(agent_id: str, task_type: str, task_description: str,
                result: str = None, success: bool = True,
                input_data: dict = {}, duration_ms: int = None,
                chat_id: int = None):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO task_journal
        (agent_id, chat_id, task_type, task_description, input_data, result, success, duration_ms)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """, (agent_id, chat_id, task_type, task_description,
          json.dumps(input_data), result, success, duration_ms))
    conn.commit()
    cur.close()
    conn.close()


def journal_get(agent_id: str, limit: int = 20, task_type: str = None) -> list:
    conn = get_conn()
    cur = conn.cursor()
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
    cur.close()
    conn.close()
    return [{
        "type": r[0], "description": r[1], "result": r[2],
        "success": r[3], "date": r[4].strftime("%Y-%m-%d %H:%M")
    } for r in rows]


# ── Agent Identity ────────────────────────────────────────────────────────────

def identity_set(agent_id: str, name: str, role: str, soul: str, system_prompt: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO agent_identity (agent_id, name, role, soul, system_prompt, updated_at)
        VALUES (%s, %s, %s, %s, %s, NOW())
        ON CONFLICT (agent_id) DO UPDATE
        SET name = EXCLUDED.name, role = EXCLUDED.role, soul = EXCLUDED.soul,
            system_prompt = EXCLUDED.system_prompt, updated_at = NOW()
    """, (agent_id, name, role, soul, system_prompt))
    conn.commit()
    cur.close()
    conn.close()


def identity_get(agent_id: str) -> Optional[dict]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT name, role, soul, system_prompt FROM agent_identity WHERE agent_id = %s",
        (agent_id,)
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return {"name": row[0], "role": row[1], "soul": row[2], "system_prompt": row[3]} if row else None


# ── Context Builder ───────────────────────────────────────────────────────────

def build_agent_context(agent_id: str) -> str:
    """
    Build a full context string for an agent's system prompt injection.
    Includes: memory, empire knowledge, recent summaries, available skills.
    NOTE: Identity/system_prompt is injected separately — we skip it here
    to avoid conflicting identity blocks confusing the LLM.
    """
    sections = []

    # Memory
    memories = memory_get_all(agent_id)
    if memories:
        mem_lines = []
        for key, data in memories.items():
            mem_lines.append(f"- [{data['category']}] {key}: {data['value']}")
        sections.append("## Your Memory\n" + "\n".join(mem_lines))

    # Empire Knowledge
    empire = empire_get_all()
    if empire:
        emp_lines = []
        for category, items in empire.items():
            emp_lines.append(f"\n### {category.upper()}")
            for k, v in items.items():
                emp_lines.append(f"  - {k}: {v}")
        sections.append("## Empire Knowledge" + "\n".join(emp_lines))

    # Recent summaries
    summaries = get_summaries(agent_id, limit=3)
    if summaries:
        sum_lines = [f"- [{s['date']}] {s['summary']}" for s in summaries]
        sections.append("## Recent Session Summaries\n" + "\n".join(sum_lines))

    # Available skills
    skills = get_skills(agent_id)
    if skills:
        skill_lines = [f"- {s['name']}: {s['description']}" for s in skills]
        sections.append("## Your Available Skills\n" + "\n".join(skill_lines))

    return "\n\n---\n\n".join(sections)
