#!/usr/bin/env python3
import psycopg2
conn = psycopg2.connect(host="localhost", port=5432, dbname="baza_agents", user="switchhacker", password="baza2026")
cur = conn.cursor()
cur.execute("""
    SELECT role, left(content, 150), created_at 
    FROM messages 
    WHERE agent_id='simon_bately' 
    ORDER BY created_at DESC LIMIT 10
""")
for row in cur.fetchall():
    print(f"[{row[2]}] {row[0]}:\n  {row[1]}\n")
cur.close()
conn.close()
