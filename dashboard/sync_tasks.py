#!/usr/bin/env python3
"""
Baza Empire — Task Sync / Re-seed
Run this any time you want to reset or inspect the local task DB.

Usage:
  python dashboard/sync_tasks.py           # show current task state
  python dashboard/sync_tasks.py --reset   # wipe and re-seed all tasks
  python dashboard/sync_tasks.py --fix     # fix status values / missing columns
"""
import os
import sys
import sqlite3
import uuid
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'baza_projects.db')

# ── Schema ─────────────────────────────────────────────────────────────────────

def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS projects (
        id TEXT PRIMARY KEY,
        name TEXT,
        description TEXT,
        status TEXT DEFAULT 'active',
        launch_date TEXT,
        owner TEXT,
        created_at TEXT,
        updated_at TEXT
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        project_id TEXT,
        parent_task_id TEXT,
        title TEXT,
        description TEXT,
        assigned_to TEXT,
        status TEXT DEFAULT 'pending',
        priority TEXT DEFAULT 'medium',
        due_date TEXT,
        notes TEXT,
        is_subtask INTEGER DEFAULT 0,
        created_at TEXT,
        updated_at TEXT
    )''')
    # Add missing columns if upgrading old DB
    for col, typedef in [
        ("is_subtask",  "INTEGER DEFAULT 0"),
        ("parent_task_id", "TEXT"),
        ("updated_at",  "TEXT"),
        ("created_at",  "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} {typedef}")
        except:
            pass
    conn.commit()


# ── Seed data ──────────────────────────────────────────────────────────────────

PROJECTS = [
    {
        "id": "proj-ahb123",
        "name": "ahb123.com",
        "description": "Company website for All Home Building Co LLC",
        "status": "active",
        "launch_date": "2026-04-01",
        "owner": "simon_bately",
    },
    {
        "id": "proj-baza-empire",
        "name": "Baza Empire",
        "description": "AI agent network, mining infrastructure, automation stack",
        "status": "active",
        "launch_date": "",
        "owner": "serge",
    },
]

TASKS = [
    # ── Claw Batto ─────────────────────────────────────────────────────────────
    {
        "project_id": "proj-ahb123",
        "title": "Squarespace template selection and setup",
        "assigned_to": "claw_batto",
        "priority": "high",
        "due_date": "2026-03-26",
        "status": "in_progress",
        "description": "Select best Squarespace template for construction/home building. Configure for ahb123.com domain.",
    },
    {
        "project_id": "proj-ahb123",
        "title": "Client funnel — Plan Your Project form",
        "assigned_to": "claw_batto",
        "priority": "high",
        "due_date": "2026-03-28",
        "status": "pending",
        "description": "Design and build project intake form with name, contact, project type, budget, timeline fields.",
    },
    {
        "project_id": "proj-ahb123",
        "title": "Find a Contractor CTA page",
        "assigned_to": "claw_batto",
        "priority": "medium",
        "due_date": "2026-03-29",
        "status": "pending",
        "description": "Build page with contractor inquiry flow, contact capture, and pipeline integration.",
    },
    {
        "project_id": "proj-ahb123",
        "title": "SEO setup — metadata, schema markup, sitemap.xml",
        "assigned_to": "claw_batto",
        "priority": "medium",
        "due_date": "2026-03-30",
        "status": "pending",
        "description": "Implement SEO across all pages, Google Search Console setup, sitemap submission.",
    },
    {
        "project_id": "proj-ahb123",
        "title": "QA and cross-device testing",
        "assigned_to": "claw_batto",
        "priority": "high",
        "due_date": "2026-03-31",
        "status": "pending",
        "description": "Test all pages on desktop, tablet, mobile. Check all CTAs, forms, gallery, chat.",
    },
    {
        "project_id": "proj-ahb123",
        "title": "Squarespace export package — ready to import",
        "assigned_to": "claw_batto",
        "priority": "high",
        "due_date": "2026-03-31",
        "status": "pending",
        "description": "Package all content, images, CSS for clean Squarespace import. Verify domain connection.",
    },
    # ── Sam Axe ────────────────────────────────────────────────────────────────
    {
        "project_id": "proj-ahb123",
        "title": "Brand identity — logo options for AHBCO LLC",
        "assigned_to": "sam_axe",
        "priority": "high",
        "due_date": "2026-03-25",
        "status": "in_progress",
        "description": "Create 3 logo concepts: modern minimal, bold industrial, clean professional.",
    },
    {
        "project_id": "proj-ahb123",
        "title": "Color palette and typography system",
        "assigned_to": "sam_axe",
        "priority": "high",
        "due_date": "2026-03-25",
        "status": "in_progress",
        "description": "Define brand colors (navy, white, warm wood, concrete grey) and font pairing.",
    },
    {
        "project_id": "proj-ahb123",
        "title": "Homepage hero image and banner",
        "assigned_to": "sam_axe",
        "priority": "high",
        "due_date": "2026-03-26",
        "status": "pending",
        "description": "Generate hero image — modern residential/commercial construction, warm lighting.",
    },
    {
        "project_id": "proj-ahb123",
        "title": "Completed work gallery — image set",
        "assigned_to": "sam_axe",
        "priority": "high",
        "due_date": "2026-03-27",
        "status": "pending",
        "description": "Generate or source 8-12 portfolio images for gallery.",
    },
    {
        "project_id": "proj-ahb123",
        "title": "Service section icons and graphics",
        "assigned_to": "sam_axe",
        "priority": "medium",
        "due_date": "2026-03-28",
        "status": "pending",
        "description": "Create icons/graphics for each service: remodeling, additions, new construction, PM.",
    },
    # ── Simon Bately ───────────────────────────────────────────────────────────
    {
        "project_id": "proj-ahb123",
        "title": "Page content — Home and About",
        "assigned_to": "simon_bately",
        "priority": "high",
        "due_date": "2026-03-26",
        "status": "in_progress",
        "description": "Write homepage copy, about page, company story, value proposition for AHBCO LLC.",
    },
    {
        "project_id": "proj-ahb123",
        "title": "Page content — Services and Process",
        "assigned_to": "simon_bately",
        "priority": "high",
        "due_date": "2026-03-27",
        "status": "pending",
        "description": "Write services page, how it works section, contractor process description.",
    },
    {
        "project_id": "proj-ahb123",
        "title": "Client chat bot — Simon as client specialist",
        "assigned_to": "simon_bately",
        "priority": "high",
        "due_date": "2026-03-29",
        "status": "pending",
        "description": "Configure Simon as live chat on ahb123.com — greet visitors, qualify leads.",
    },
    {
        "project_id": "proj-ahb123",
        "title": "Lead pipeline — intake to invoice workflow",
        "assigned_to": "simon_bately",
        "priority": "high",
        "due_date": "2026-03-28",
        "status": "pending",
        "description": "Map full client journey: chat → form → project created → invoice → follow-up.",
    },
    # ── Phil Hass ──────────────────────────────────────────────────────────────
    {
        "project_id": "proj-ahb123",
        "title": "DBA Registration — AHBCO LLC",
        "assigned_to": "phil_hass",
        "priority": "high",
        "due_date": "2026-03-27",
        "status": "in_progress",
        "description": "Research PA DBA requirements, prepare docs, file registration for AHBCO LLC.",
    },
    {
        "project_id": "proj-ahb123",
        "title": "Website terms of service and privacy policy",
        "assigned_to": "phil_hass",
        "priority": "high",
        "due_date": "2026-03-28",
        "status": "pending",
        "description": "Draft ToS and privacy policy compliant with PA law and GDPR basics.",
    },
    {
        "project_id": "proj-ahb123",
        "title": "Contractor agreement template",
        "assigned_to": "phil_hass",
        "priority": "medium",
        "due_date": "2026-03-30",
        "status": "pending",
        "description": "Draft standard subcontractor agreement for AHBCO LLC.",
    },
    # ── Duke Harmon ────────────────────────────────────────────────────────────
    {
        "project_id": "proj-ahb123",
        "title": "Launch countdown and deadline tracker",
        "assigned_to": "duke_harmon",
        "priority": "high",
        "due_date": "2026-03-25",
        "status": "in_progress",
        "description": "Track all tasks, flag blockers, enforce April 1 deadline across all agents.",
    },
    # ── Rex Valor ──────────────────────────────────────────────────────────────
    {
        "project_id": "proj-ahb123",
        "title": "Voicemail intake script — AHBCO LLC",
        "assigned_to": "rex_valor",
        "priority": "medium",
        "due_date": "2026-03-28",
        "status": "pending",
        "description": "Create voicemail greeting and lead qualification script for inbound calls.",
    },
    # ── Scout Reeves ───────────────────────────────────────────────────────────
    {
        "project_id": "proj-ahb123",
        "title": "Competitor research — home building websites PA",
        "assigned_to": "scout_reeves",
        "priority": "medium",
        "due_date": "2026-03-26",
        "status": "pending",
        "description": "Research top 5 competitor home building websites in PA. What do they do well?",
    },
]


def show_status(conn):
    tasks = conn.execute("SELECT status, COUNT(*) as c FROM tasks GROUP BY status").fetchall()
    total = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    print(f"\nTask DB Status ({DB_PATH}):")
    print(f"  Total: {total}")
    for row in tasks:
        print(f"  {row[0]}: {row[1]}")
    done = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='completed'").fetchone()[0]
    pct  = round((done / total) * 100) if total else 0
    print(f"  Progress: {pct}%")


def reset_and_seed(conn):
    now = datetime.now().isoformat()
    print("Wiping existing tasks and projects...")
    conn.execute("DELETE FROM tasks")
    conn.execute("DELETE FROM projects")

    for p in PROJECTS:
        conn.execute('''INSERT INTO projects (id, name, description, status, launch_date, owner, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                     (p['id'], p['name'], p['description'], p['status'],
                      p.get('launch_date', ''), p.get('owner', ''), now, now))

    for t in TASKS:
        task_id = str(uuid.uuid4())
        conn.execute('''INSERT INTO tasks
            (id, project_id, title, description, assigned_to, status, priority, due_date, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (task_id, t['project_id'], t['title'], t.get('description', ''),
             t['assigned_to'], t.get('status', 'pending'), t.get('priority', 'medium'),
             t.get('due_date', ''), t.get('notes', ''), now, now))

    conn.commit()
    print(f"Seeded {len(PROJECTS)} projects and {len(TASKS)} tasks.")


def fix_schema(conn):
    """Add missing columns to existing DB without wiping data."""
    for col, typedef in [
        ("updated_at", "TEXT"),
        ("created_at", "TEXT"),
        ("is_subtask",  "INTEGER DEFAULT 0"),
        ("parent_task_id", "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} {typedef}")
            print(f"Added column: {col}")
        except:
            pass
    # Fix NULL updated_at
    conn.execute("UPDATE tasks SET updated_at=datetime('now') WHERE updated_at IS NULL")
    conn.execute("UPDATE tasks SET created_at=datetime('now') WHERE created_at IS NULL")
    conn.commit()
    print("Schema fix complete.")


if __name__ == "__main__":
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)

    if "--reset" in sys.argv:
        reset_and_seed(conn)
        show_status(conn)
    elif "--fix" in sys.argv:
        fix_schema(conn)
        show_status(conn)
    else:
        show_status(conn)
        print("\nRun with --reset to wipe and re-seed, --fix to repair schema.")

    conn.close()
