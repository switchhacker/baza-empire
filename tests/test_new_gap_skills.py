"""Tests for the gap-filling shared skills (claw_findings, bin_list,
social_draft, knowledge_add) added 2026-07-01."""
import json
import os
import sqlite3
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(REPO_ROOT, "skills", "shared")


def run_skill(name, args, extra_env=None):
    env = os.environ.copy()
    env["SKILL_ARGS"] = json.dumps(args)
    env["AGENT_ID"] = "test_agent"
    env.update(extra_env or {})
    r = subprocess.run(
        [sys.executable, os.path.join(SKILLS, name)],
        capture_output=True, text=True, timeout=30, env=env,
    )
    return r


def test_skills_have_meta_and_compile():
    for name in ("claw_findings.py", "bin_list.py", "social_draft.py", "knowledge_add.py"):
        src = open(os.path.join(SKILLS, name)).read()
        compile(src, name, "exec")
        assert "SKILL_META" in src, name
        assert "SKILL_ARGS" in src, name


def test_claw_findings_returns_rows():
    r = run_skill("claw_findings.py", {"limit": 3})
    assert r.returncode == 0, r.stderr
    j = json.loads(r.stdout)
    assert "findings" in j and "count" in j
    assert j["count"] <= 3


def test_claw_findings_counts_mode():
    r = run_skill("claw_findings.py", {"counts": True})
    assert r.returncode == 0, r.stderr
    j = json.loads(r.stdout)
    assert "severity_counts" in j


def test_bin_list_with_temp_db(tmp_path):
    db = tmp_path / "bin.db"
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE bin_files (
        id TEXT PRIMARY KEY, name TEXT, stored_path TEXT, size INTEGER,
        mime_type TEXT, kind TEXT, caption TEXT, source TEXT,
        tg_user_id TEXT, created_at TEXT DEFAULT (datetime('now')))""")
    con.execute(
        "INSERT INTO bin_files (id, name, stored_path, size, mime_type, kind, caption, source, tg_user_id)"
        " VALUES ('abc123','plan.pdf','/x/plan.pdf',10,'application/pdf','document','kitchen plan','telegram','1')")
    con.commit(); con.close()
    r = run_skill("bin_list.py", {"q": "kitchen"}, extra_env={"BAZA_BIN_DB": str(db)})
    assert r.returncode == 0, r.stderr
    j = json.loads(r.stdout)
    assert j["count"] == 1
    assert j["items"][0]["name"] == "plan.pdf"
    # api-contract fields only — no raw paths / tg ids leaked
    assert "stored_path" not in j["items"][0]
    assert "tg_user_id" not in j["items"][0]


def test_social_draft_rejects_bad_platform():
    r = run_skill("social_draft.py", {"platform": "facebook", "caption": "hi"})
    assert r.returncode == 1
    assert "platform" in json.loads(r.stdout)["error"]


def test_social_draft_requires_caption():
    r = run_skill("social_draft.py", {"platform": "tiktok"})
    assert r.returncode == 1
    assert "caption" in json.loads(r.stdout)["error"]


def test_social_draft_unreachable_dashboard_is_clean_error():
    r = run_skill("social_draft.py", {"platform": "tiktok", "caption": "test"},
                  extra_env={"BAZA_DASHBOARD_URL": "http://127.0.0.1:1"})
    assert r.returncode == 1
    assert "unreachable" in json.loads(r.stdout)["error"]


def test_knowledge_add_requires_key_and_value():
    r = run_skill("knowledge_add.py", {"key": "", "value": ""})
    assert r.returncode == 1
    assert "required" in json.loads(r.stdout)["error"]


def test_knowledge_add_rejects_oversize_value():
    r = run_skill("knowledge_add.py", {"key": "k", "value": "x" * 5000})
    assert r.returncode == 1
    assert "too long" in json.loads(r.stdout)["error"]
