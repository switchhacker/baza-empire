#!/usr/bin/env python3
"""Add a link to the Baza dashboard links section."""
import os, json, sqlite3, uuid

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
title = args.get("title", "")
url = args.get("url", "")
if not title or not url:
    print(json.dumps({"error": "title and url are required"}))
    exit()

db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../dashboard/baza_projects.db")
conn = sqlite3.connect(db)
lid = str(uuid.uuid4())
conn.execute(
    "INSERT INTO baza_dash_links (id,title,url,icon,category,sort_order) VALUES (?,?,?,?,?,?)",
    (lid, title, url, args.get("icon","&#128279;"), args.get("category","general"),
     args.get("sort_order", 99)))
conn.commit()
conn.close()
print(json.dumps({"id": lid, "title": title, "url": url, "status": "link added"}))
