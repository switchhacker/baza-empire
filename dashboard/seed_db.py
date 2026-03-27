#!/usr/bin/env python3
"""
Baza Empire — Local DB Seed
Run once to populate baza_projects.db with all project/task data.
After this the dashboard runs 100% locally — no external API calls ever.
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'baza_projects.db')

PROJECTS = [
    {
        'id': '69c2bce7928bb0babb49a0a5',
        'name': 'ahb123.com Website Launch',
        'description': 'Full website build for All Home Building Co LLC. Modern design, curated pages, client funnel, work gallery, chat, Squarespace export. Launch April 1 2026.',
        'status': 'active',
        'launch_date': '2026-04-01',
        'owner': 'serge',
    },
]

TASKS = [
    # ── Claw Batto ──────────────────────────────────────────────────────────
    {'id':'69c2bd07928bb0babb49a0c3','project_id':'69c2bce7928bb0babb49a0a5','title':'Squarespace template selection and setup','description':'Select best Squarespace template for construction/home building. Configure for ahb123.com domain.','assigned_to':'claw_batto','status':'pending','priority':'high','due_date':'2026-03-26','notes':None},
    {'id':'69c2bd07928bb0babb49a0c4','project_id':'69c2bce7928bb0babb49a0a5','title':'Client funnel — Plan Your Project form','description':'Design and build project intake form with name, contact, project type, budget, timeline fields.','assigned_to':'claw_batto','status':'pending','priority':'high','due_date':'2026-03-28','notes':None},
    {'id':'69c2bd07928bb0babb49a0c5','project_id':'69c2bce7928bb0babb49a0a5','title':'Find a Contractor CTA page','description':'Build page with contractor inquiry flow, contact capture, and pipeline integration','assigned_to':'claw_batto','status':'pending','priority':'medium','due_date':'2026-03-29','notes':None},
    {'id':'69c2bd07928bb0babb49a0c6','project_id':'69c2bce7928bb0babb49a0a5','title':'SEO setup — metadata, schema markup, sitemap.xml','description':'Implement SEO across all pages, Google Search Console setup, sitemap submission','assigned_to':'claw_batto','status':'pending','priority':'medium','due_date':'2026-03-30','notes':None},
    {'id':'69c2bd07928bb0babb49a0d0','project_id':'69c2bce7928bb0babb49a0a5','title':'QA and cross-device testing','description':'Test all pages on desktop, tablet, mobile. Check all CTAs, forms, gallery, chat functionality.','assigned_to':'claw_batto','status':'pending','priority':'high','due_date':'2026-03-31','notes':None},
    {'id':'69c2bd07928bb0babb49a0d1','project_id':'69c2bce7928bb0babb49a0a5','title':'Squarespace export package — ready to import','description':'Package all content, images, CSS for clean Squarespace import. Verify domain connection.','assigned_to':'claw_batto','status':'pending','priority':'high','due_date':'2026-03-31','notes':None},
    # ── Sam Axe ─────────────────────────────────────────────────────────────
    {'id':'69c2bd07928bb0babb49a0c7','project_id':'69c2bce7928bb0babb49a0a5','title':'Brand identity — logo options for AHBCO LLC','description':'Create 3 logo concepts: modern minimal, bold industrial, clean professional. Present for approval.','assigned_to':'sam_axe','status':'in_progress','priority':'high','due_date':'2026-03-25','notes':None},
    {'id':'69c2bd07928bb0babb49a0c8','project_id':'69c2bce7928bb0babb49a0a5','title':'Color palette and typography system','description':'Define brand colors (navy, white, warm wood, concrete grey) and font pairing for site','assigned_to':'sam_axe','status':'in_progress','priority':'high','due_date':'2026-03-25','notes':None},
    {'id':'69c2bd07928bb0babb49a0c9','project_id':'69c2bce7928bb0babb49a0a5','title':'Homepage hero image and banner','description':'Generate hero image for homepage — modern residential/commercial construction, professional, warm lighting','assigned_to':'sam_axe','status':'pending','priority':'high','due_date':'2026-03-26','notes':None},
    {'id':'69c2bd07928bb0babb49a0ca','project_id':'69c2bce7928bb0babb49a0a5','title':'Completed work gallery — image set','description':'Generate or source 8-12 portfolio images showing residential remodels, additions, new builds for gallery','assigned_to':'sam_axe','status':'pending','priority':'high','due_date':'2026-03-27','notes':None},
    {'id':'69c2bd07928bb0babb49a0cb','project_id':'69c2bce7928bb0babb49a0a5','title':'Service section icons and graphics','description':'Create icons/graphics for each service: remodeling, additions, new construction, project management','assigned_to':'sam_axe','status':'pending','priority':'medium','due_date':'2026-03-28','notes':None},
    # ── Simon Bately ────────────────────────────────────────────────────────
    {'id':'69c2bd07928bb0babb49a0cc','project_id':'69c2bce7928bb0babb49a0a5','title':'Page content — Home and About','description':'Write homepage copy, about page, company story, value proposition for AHBCO LLC','assigned_to':'simon_bately','status':'in_progress','priority':'high','due_date':'2026-03-26','notes':None},
    {'id':'69c2bd07928bb0babb49a0cd','project_id':'69c2bce7928bb0babb49a0a5','title':'Page content — Services and Process','description':'Write services page, how it works section, contractor process description','assigned_to':'simon_bately','status':'pending','priority':'high','due_date':'2026-03-27','notes':None},
    {'id':'69c2bd07928bb0babb49a0ce','project_id':'69c2bce7928bb0babb49a0a5','title':'Client chat bot — Simon as client specialist','description':'Configure Simon as live chat on ahb123.com — greet visitors, qualify leads, funnel to pipeline','assigned_to':'simon_bately','status':'pending','priority':'high','due_date':'2026-03-29','notes':None},
    {'id':'69c2bd07928bb0babb49a0cf','project_id':'69c2bce7928bb0babb49a0a5','title':'Lead pipeline — intake to invoice workflow','description':'Map full client journey: chat → form → project created → invoice generated → follow-up','assigned_to':'simon_bately','status':'pending','priority':'high','due_date':'2026-03-28','notes':None},
    # ── Phil Hass ───────────────────────────────────────────────────────────
    {'id':'69c2bd07928bb0babb49a0be','project_id':'69c2bce7928bb0babb49a0a5','title':'DBA Registration — AHBCO LLC','description':'Research PA DBA requirements, prepare docs, file registration for AHBCO LLC','assigned_to':'phil_hass','status':'in_progress','priority':'high','due_date':'2026-03-27','notes':None},
    {'id':'69c2bd07928bb0babb49a0bf','project_id':'69c2bce7928bb0babb49a0a5','title':'Operating Agreement draft','description':'Draft LLC Operating Agreement for All Home Building Co LLC — PA compliant','assigned_to':'phil_hass','status':'pending','priority':'high','due_date':'2026-03-28','notes':None},
    {'id':'69c2bd07928bb0babb49a0c0','project_id':'69c2bce7928bb0babb49a0a5','title':'EIN application — IRS Form SS-4','description':'Apply for Employer Identification Number for AHBCO LLC via IRS online','assigned_to':'phil_hass','status':'pending','priority':'high','due_date':'2026-03-29','notes':None},
    {'id':'69c2bd07928bb0babb49a0c1','project_id':'69c2bce7928bb0babb49a0a5','title':'Business bank account setup','description':'Open business checking account for AHBCO LLC — recommend Chase Business Complete','assigned_to':'phil_hass','status':'pending','priority':'medium','due_date':'2026-03-30','notes':None},
    # ── Duke Harmon ─────────────────────────────────────────────────────────
    {'id':'69c2bd07928bb0babb49a0c2','project_id':'69c2bce7928bb0babb49a0a5','title':'Project timeline and milestone tracking','description':'Build master project timeline with all deliverables, owners, deadlines. Track daily.','assigned_to':'duke_harmon','status':'in_progress','priority':'high','due_date':'2026-03-25','notes':None},
    # ── Rex Valor ───────────────────────────────────────────────────────────
    {'id':'69c2bd07928bb0babb49a0d2','project_id':'69c2bce7928bb0babb49a0a5','title':'Voicemail system — 800-484-6404 integration','description':'Configure Rex Valor as voicemail agent on 800-484-6404. Capture leads, log to pipeline.','assigned_to':'rex_valor','status':'pending','priority':'high','due_date':'2026-03-30','notes':None},
]

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute('''
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT,
            description TEXT,
            status TEXT DEFAULT 'active',
            launch_date TEXT,
            owner TEXT
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            title TEXT,
            description TEXT,
            assigned_to TEXT,
            status TEXT DEFAULT 'pending',
            priority TEXT DEFAULT 'medium',
            due_date TEXT,
            notes TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS emails (
            id TEXT PRIMARY KEY,
            gmail_id TEXT,
            thread_id TEXT,
            from_addr TEXT,
            subject TEXT,
            body_snippet TEXT,
            full_body TEXT,
            received_at TEXT,
            status TEXT DEFAULT 'new',
            summary TEXT,
            suggested_reply TEXT,
            priority TEXT,
            labels TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    ''')

    conn.commit()

    # Seed projects
    for p in PROJECTS:
        cur.execute('''
            INSERT OR IGNORE INTO projects (id, name, description, status, launch_date, owner)
            VALUES (:id, :name, :description, :status, :launch_date, :owner)
        ''', p)

    # Seed tasks
    for t in TASKS:
        cur.execute('''
            INSERT OR IGNORE INTO tasks (id, project_id, title, description, assigned_to, status, priority, due_date, notes)
            VALUES (:id, :project_id, :title, :description, :assigned_to, :status, :priority, :due_date, :notes)
        ''', t)

    conn.commit()
    conn.close()
    print(f"[seed_db] Done. {len(PROJECTS)} projects, {len(TASKS)} tasks seeded into {DB_PATH}")

if __name__ == '__main__':
    init_db()
