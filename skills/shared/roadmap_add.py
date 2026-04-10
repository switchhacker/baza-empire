#!/usr/bin/env python3
"""Add an item to the Baza Empire roadmap."""
import os, json, sqlite3, uuid

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
title = args.get("title", "")
if not title:
    print(json.dumps({"error": "title is required"}))
    exit()

db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../dashboard/baza_projects.db")
conn = sqlite3.connect(db)
rid = str(uuid.uuid4())
conn.execute(
    "INSERT INTO baza_roadmap (id,title,description,status,priority,category,assigned_agent,target_date,notes) VALUES (?,?,?,?,?,?,?,?,?)",
    (rid, title, args.get("description",""), args.get("status","planned"),
     args.get("priority","medium"), args.get("category","general"),
     args.get("assigned_agent",""), args.get("target_date",""), args.get("notes","")))
conn.commit()
conn.close()
print(json.dumps({"id": rid, "title": title, "status": "added to roadmap"}))
