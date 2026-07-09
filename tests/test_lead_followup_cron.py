# tests/test_lead_followup_cron.py — Rex's lead_followup cron vs the REAL schema.
# Regression for the 2026-07-08 systemd-cutover failure: the events query used
# start_date/type columns that don't exist in ahb_events (schema has date/category).
import importlib.util
import os
import sqlite3
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)


def _load_cron():
    spec = importlib.util.spec_from_file_location(
        "lead_followup", os.path.join(REPO_ROOT, "agents", "rex_valor", "crons", "lead_followup.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _real_schema_db(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
      CREATE TABLE ahb_clients (name TEXT, phone TEXT, email TEXT, source TEXT,
                                created_at TEXT, status TEXT);
      CREATE TABLE ahb_events (id TEXT PRIMARY KEY, title TEXT, details TEXT,
                               date TEXT, time TEXT, end_time TEXT, category TEXT,
                               all_day INTEGER DEFAULT 0, project_id TEXT,
                               employee_id TEXT, created_at TEXT,
                               phase_id TEXT DEFAULT '', task_id TEXT DEFAULT '');
      INSERT INTO ahb_clients VALUES ('Lead Larry','215-000-0000','l@x.com','web','2026-07-01','lead');
      INSERT INTO ahb_events (id,title,date,category) VALUES
        ('e1','Call back Larry re: deck','2026-07-07','Reminder'),
        ('e2','Meeting with supplier','2026-07-06','Business'),
        ('e3','Pay quarterly taxes','2026-07-05','Deadline');
    """)
    conn.commit()
    conn.close()


def test_collect_data_works_against_real_schema(tmp_path, monkeypatch):
    db = str(tmp_path / "b.db")
    _real_schema_db(db)
    mod = _load_cron()
    monkeypatch.setattr(mod, "get_db", lambda: sqlite3.connect(db))
    data = mod.collect_data()
    assert "Lead Larry" in data
    # call/meeting events are surfaced by title keyword (schema has no 'type' col)
    assert "Call back Larry" in data
    assert "Meeting with supplier" in data
    assert "Pay quarterly taxes" not in data
