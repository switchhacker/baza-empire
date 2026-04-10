#!/usr/bin/env python3
"""Create a new task in the Baza task board."""
import os, json, sqlite3, uuid

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
title = args.get("title", "")
if not title:
    print(json.dumps({"error": "title is required"}))
    exit()

db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../dashboard/baza_projects.db")
conn = sqlite3.connect(db)
tid = str(uuid.uuid4())
conn.execute(
    "INSERT INTO tasks (id, title, description, status, priority, assignee, project_id, created_at) VALUES (?,?,?,?,?,?,?,datetime('now'))",
    (tid, title, args.get("description",""), args.get("status","pending"),
     args.get("priority","medium"), args.get("assignee",""), args.get("project_id","")))
conn.commit()
conn.close()
print(json.dumps({"id": tid, "title": title, "status": "task created"}))
