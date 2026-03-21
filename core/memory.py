import psycopg2
import os

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": "baza_agents",
    "user": "switchhacker",
    "password": os.environ.get("DB_PASSWORD", "baza2026")
}

def get_conn():
    return psycopg2.connect(**DB_CONFIG)

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY,
            chat_id BIGINT NOT NULL,
            agent_id VARCHAR(50),
            agent_name VARCHAR(50),
            role VARCHAR(20),
            content TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS tasks (
            id SERIAL PRIMARY KEY,
            chat_id BIGINT NOT NULL,
            agent_id VARCHAR(50),
            task TEXT,
            status VARCHAR(20) DEFAULT 'active',
            created_at TIMESTAMP DEFAULT NOW(),
            completed_at TIMESTAMP
        );
    """)
    # Add agent_id column if it doesn't exist (migration for existing DBs)
    try:
        cur.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS agent_id VARCHAR(50);")
        cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS agent_id VARCHAR(50);")
    except Exception:
        pass
    conn.commit()
    cur.close()
    conn.close()

def save_message(chat_id, agent_id, role, content):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO messages (chat_id, agent_id, agent_name, role, content) VALUES (%s, %s, %s, %s, %s)",
        (chat_id, agent_id, agent_id, role, content)
    )
    conn.commit()
    cur.close()
    conn.close()

def get_history(chat_id, agent_id=None, limit=20):
    """Get conversation history, scoped to this agent's chat_id + agent_id."""
    conn = get_conn()
    cur = conn.cursor()
    if agent_id:
        cur.execute(
            "SELECT agent_id, role, content FROM messages WHERE chat_id = %s AND agent_id = %s ORDER BY created_at DESC LIMIT %s",
            (chat_id, agent_id, limit)
        )
    else:
        cur.execute(
            "SELECT agent_id, role, content FROM messages WHERE chat_id = %s ORDER BY created_at DESC LIMIT %s",
            (chat_id, limit)
        )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [{"agent": r[0], "role": r[1], "content": r[2]} for r in reversed(rows)]

def get_active_task(chat_id, agent_id=None):
    conn = get_conn()
    cur = conn.cursor()
    if agent_id:
        cur.execute(
            "SELECT task FROM tasks WHERE chat_id = %s AND agent_id = %s AND status = 'active' ORDER BY created_at DESC LIMIT 1",
            (chat_id, agent_id)
        )
    else:
        cur.execute(
            "SELECT task FROM tasks WHERE chat_id = %s AND status = 'active' ORDER BY created_at DESC LIMIT 1",
            (chat_id,)
        )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else None

def set_task(chat_id, task, agent_id=None):
    conn = get_conn()
    cur = conn.cursor()
    if agent_id:
        cur.execute(
            "UPDATE tasks SET status = 'completed', completed_at = NOW() WHERE chat_id = %s AND agent_id = %s AND status = 'active'",
            (chat_id, agent_id)
        )
        cur.execute("INSERT INTO tasks (chat_id, agent_id, task) VALUES (%s, %s, %s)", (chat_id, agent_id, task))
    else:
        cur.execute(
            "UPDATE tasks SET status = 'completed', completed_at = NOW() WHERE chat_id = %s AND status = 'active'",
            (chat_id,)
        )
        cur.execute("INSERT INTO tasks (chat_id, task) VALUES (%s, %s)", (chat_id, task))
    conn.commit()
    cur.close()
    conn.close()

def complete_task(chat_id, agent_id=None):
    conn = get_conn()
    cur = conn.cursor()
    if agent_id:
        cur.execute(
            "UPDATE tasks SET status = 'completed', completed_at = NOW() WHERE chat_id = %s AND agent_id = %s AND status = 'active'",
            (chat_id, agent_id)
        )
    else:
        cur.execute(
            "UPDATE tasks SET status = 'completed', completed_at = NOW() WHERE chat_id = %s AND status = 'active'",
            (chat_id,)
        )
    conn.commit()
    cur.close()
    conn.close()
