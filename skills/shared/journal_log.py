#!/usr/bin/env python3
"""Write an entry to the agent task journal (PostgreSQL)."""
import os, json

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
agent_id = args.get("agent_id", "system")
action = args.get("action", "")
detail = args.get("detail", "")

if not action:
    print(json.dumps({"error": "action is required"}))
    exit()

try:
    import psycopg2
    conn = psycopg2.connect(
        host="localhost", port=5432, dbname="baza_agents",
        user="baza", password=os.environ.get("DB_PASSWORD", ""))
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO task_journal (agent_id, task_type, detail) VALUES (%s, %s, %s)",
        (agent_id, action, detail))
    conn.commit()
    conn.close()
    print(json.dumps({"status": "logged", "agent": agent_id, "action": action}))
except Exception as e:
    print(json.dumps({"error": str(e)}))
