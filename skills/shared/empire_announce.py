#!/usr/bin/env python3
"""Post a system-wide announcement to empire_knowledge (PostgreSQL)."""
import os, json

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
message = args.get("message", "")
category = args.get("category", "announcement")

if not message:
    print(json.dumps({"error": "message is required"}))
    exit()

try:
    import psycopg2
    conn = psycopg2.connect(
        host="localhost", port=5432, dbname="baza_agents",
        user="baza", password=os.environ.get("DB_PASSWORD", ""))
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO empire_knowledge (key, value, category, source) VALUES (%s, %s, %s, %s)",
        (f"announce_{category}", message, category, args.get("source", "system")))
    conn.commit()
    conn.close()
    print(json.dumps({"status": "announced", "category": category, "message": message[:100]}))
except Exception as e:
    print(json.dumps({"error": str(e)}))
