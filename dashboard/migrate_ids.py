#!/usr/bin/env python3
"""
Baza Empire — Migrate task IDs to proper UUIDs
Run once to fix the Base44-style ID collision problem.
All tasks get fresh UUIDs so the 8-char log prefix is always unique.

Usage: python dashboard/migrate_ids.py
"""
import os, sqlite3, uuid
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'baza_projects.db')

def migrate():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    tasks = conn.execute("SELECT * FROM tasks").fetchall()
    now   = datetime.now().isoformat()

    print(f"Found {len(tasks)} tasks. Migrating IDs to UUID...")
    for t in tasks:
        old_id  = t['id']
        new_id  = str(uuid.uuid4())
        conn.execute("UPDATE tasks SET id=?, updated_at=? WHERE id=?", (new_id, now, old_id))
        print(f"  {old_id[:12]}... → {new_id[:8]}...")

    conn.commit()
    conn.close()
    print("Migration complete. All task IDs are now unique UUIDs.")

if __name__ == "__main__":
    migrate()
