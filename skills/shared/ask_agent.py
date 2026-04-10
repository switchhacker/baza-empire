#!/usr/bin/env python3
"""Ask another agent for help by creating a task and publishing an event."""
import os, json, sqlite3, uuid, datetime

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
target_agent = args.get("agent", "")
question = args.get("question", "")
context = args.get("context", "")
from_agent = args.get("from", "unknown")

if not target_agent or not question:
    print(json.dumps({"error": "agent and question are required"}))
    exit()

db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../dashboard/baza_projects.db")
conn = sqlite3.connect(db)
tid = str(uuid.uuid4())
title = f"Help request from {from_agent}: {question[:80]}"
desc = f"Agent {from_agent} needs help:\n\n{question}\n\nContext: {context}" if context else question

conn.execute(
    "INSERT INTO tasks (id, title, description, status, priority, assignee, created_at) VALUES (?,?,?,?,?,?,?)",
    (tid, title, desc, "pending", "high", target_agent, datetime.datetime.now().isoformat()))
conn.commit()
conn.close()

# Publish event
try:
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))
    from core.event_bus import publish_sync
    publish_sync(from_agent, "agent_help_request", {
        "from": from_agent, "target": target_agent,
        "question": question, "context": context, "task_id": tid
    })
except Exception:
    pass

print(json.dumps({"task_id": tid, "target": target_agent, "status": "help request sent"}))
