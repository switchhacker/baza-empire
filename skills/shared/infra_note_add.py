#!/usr/bin/env python3
"""Add a note to a section of the infra page."""
import os, json, sqlite3, uuid

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
note = args.get("note", "")
if not note:
    print(json.dumps({"error": "note is required"}))
    exit()

db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../dashboard/baza_projects.db")
conn = sqlite3.connect(db)
nid = str(uuid.uuid4())
conn.execute("INSERT INTO baza_infra_notes (id,section,note,author) VALUES (?,?,?,?)",
             (nid, args.get("section","general"), note, args.get("author","agent")))
conn.commit()
conn.close()
print(json.dumps({"id": nid, "status": "note added", "section": args.get("section","general")}))
