"""Tests for Thumbtack/Angi lead + review intake.

Gmail and local-LLM boundaries are monkeypatched; no network, no LLM.
"""
import json
import os
import sqlite3
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def env(monkeypatch, tmp_path):
    db = os.path.join(str(tmp_path), "baza_projects.db")
    monkeypatch.setenv("BAZA_DASHBOARD_DB", db)
    sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))
    sys.modules.pop("lead_intake", None)
    import lead_intake
    lead_intake._ensure_tables(db)
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(lead_intake.lead_bp)
    yield app.test_client(), lead_intake, db
    sys.modules.pop("lead_intake", None)


def test_leads_list_empty(env):
    c, li, _ = env
    r = c.get("/api/ahb/leads")
    assert r.status_code == 200
    assert r.get_json()["items"] == []


def test_tables_exist(env):
    c, li, db = env
    con = sqlite3.connect(db)
    names = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert {"ahb_leads", "ahb_reviews", "lead_intake_state"} <= names


# --- Task 2: upsert helpers + dedup + low-rating flag ---

def test_upsert_lead_and_dedup(env):
    c, li, db = env
    msg = {"gmail_id": "g1", "received_at": "2026-06-28T10:00:00Z",
           "account_email": "contactahbco@gmail.com"}
    parsed = {"kind": "lead", "customer_name": "Jane Doe",
              "service_type": "Bathroom remodel", "location": "Newark NJ",
              "zip": "07102", "budget": "$5k-10k", "details": "Master bath",
              "contact_phone": "555-1212", "contact_email": "jane@x.com"}
    id1 = li._upsert_lead("thumbtack", msg, parsed)
    id2 = li._upsert_lead("thumbtack", msg, parsed)  # same (platform,gmail_id)
    assert id1 == id2  # dedup, not a second row
    con = sqlite3.connect(db)
    n = con.execute("SELECT COUNT(*) FROM ahb_leads").fetchone()[0]
    row = con.execute("SELECT customer_name, status, contact_email FROM ahb_leads "
                      "WHERE id=?", (id1,)).fetchone()
    con.close()
    assert n == 1
    assert row == ("Jane Doe", "new", "jane@x.com")


def test_upsert_review_flags_low_rating(env):
    c, li, db = env
    msg = {"gmail_id": "r1", "account_email": "contactahbco@gmail.com"}
    li._upsert_review("angi", msg,
                      {"kind": "review", "reviewer_name": "Bob",
                       "rating": 2, "review_text": "Late", "review_date": "2026-06-01"})
    li._upsert_review("angi", msg, {"kind": "review", "reviewer_name": "Bob",
                      "rating": 2, "review_text": "Late", "review_date": "2026-06-01"})
    con = sqlite3.connect(db)
    n = con.execute("SELECT COUNT(*) FROM ahb_reviews").fetchone()[0]
    flagged = con.execute("SELECT flagged_low, rating FROM ahb_reviews "
                          "WHERE gmail_id='r1'").fetchone()
    con.close()
    assert n == 1  # dedup
    assert flagged[0] == 1 and flagged[1] == 2.0


def test_already_seen(env):
    c, li, db = env
    assert li._already_seen("thumbtack", "x9") is False
    li._upsert_lead("thumbtack", {"gmail_id": "x9", "account_email": "a"},
                    {"kind": "lead", "customer_name": "Z"})
    assert li._already_seen("thumbtack", "x9") is True


# --- Task 3: sync() orchestration + POST /api/ahb/leads/sync ---

def _fixtures():
    return {
        "thumbtack": [
            {"gmail_id": "t-lead-1", "from_addr": "leads@thumbtack.com",
             "subject": "New lead", "received_at": "2026-06-28T09:00:00Z",
             "body": "Jane wants a bathroom remodel in Newark"},
            {"gmail_id": "t-other", "from_addr": "news@thumbtack.com",
             "subject": "Tips", "received_at": "2026-06-27T09:00:00Z",
             "body": "Marketing tips"},
        ],
        "angi": [
            {"gmail_id": "a-rev-1", "from_addr": "reviews@angi.com",
             "subject": "New review", "received_at": "2026-06-26T09:00:00Z",
             "body": "Bob rated you 5 stars: great work"},
        ],
    }


def _fake_parse(platform, msg):
    if msg["gmail_id"] == "t-lead-1":
        return {"kind": "lead", "customer_name": "Jane",
                "service_type": "Bathroom remodel", "location": "Newark"}
    if msg["gmail_id"] == "a-rev-1":
        return {"kind": "review", "reviewer_name": "Bob", "rating": 5,
                "review_text": "great work", "review_date": "2026-06-26"}
    return {"kind": "other"}


def _wire_sync(monkeypatch, li, fixtures=None):
    fx = fixtures if fixtures is not None else _fixtures()
    monkeypatch.setattr(li, "_gmail_search",
                        lambda account, senders, since: fx.get(
                            "thumbtack" if "thumbtack" in senders[0] else "angi", []))
    monkeypatch.setattr(li, "_parse_email", _fake_parse)


def test_sync_creates_leads_and_reviews(env, monkeypatch):
    c, li, db = env
    _wire_sync(monkeypatch, li)
    res = li.sync(accounts=["contactahbco@gmail.com"])
    assert res["leads_new"] == 1
    assert res["reviews_new"] == 1
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM ahb_leads").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM ahb_reviews").fetchone()[0] == 1
    con.close()


def test_sync_is_idempotent(env, monkeypatch):
    c, li, db = env
    _wire_sync(monkeypatch, li)
    li.sync(accounts=["contactahbco@gmail.com"])
    res2 = li.sync(accounts=["contactahbco@gmail.com"])
    assert res2["leads_new"] == 0 and res2["reviews_new"] == 0
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM ahb_leads").fetchone()[0] == 1
    con.close()


def test_sync_parse_failure_does_not_crash(env, monkeypatch):
    c, li, db = env
    monkeypatch.setattr(li, "_gmail_search",
                        lambda account, senders, since: [
                            {"gmail_id": "boom", "from_addr": "x@thumbtack.com",
                             "subject": "s", "received_at": "t", "body": "b"}]
                        if "thumbtack" in senders[0] else [])

    def boom(platform, msg):
        raise ValueError("bad json")
    monkeypatch.setattr(li, "_parse_email", boom)
    res = li.sync(accounts=["contactahbco@gmail.com"])
    assert res["leads_new"] == 0 and res["reviews_new"] == 0
    assert res["errors"]  # recorded, not raised
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM ahb_leads").fetchone()[0] == 0
    con.close()


def test_sync_does_not_advance_cursor_on_error(env, monkeypatch):
    c, li, db = env

    def search(account, senders, since):
        if "thumbtack" in senders[0]:
            raise RuntimeError("token expired")
        return []
    monkeypatch.setattr(li, "_gmail_search", search)
    monkeypatch.setattr(li, "_parse_email", lambda p, m: {"kind": "other"})
    res = li.sync(accounts=["contactahbco@gmail.com"])
    assert res["errors"]
    con = sqlite3.connect(db)
    row = con.execute("SELECT last_synced_epoch FROM lead_intake_state "
                      "WHERE account_email=?", ("contactahbco@gmail.com",)).fetchone()
    con.close()
    assert row is None  # cursor NOT advanced because the account had an error


def test_sync_route(env, monkeypatch):
    c, li, db = env
    _wire_sync(monkeypatch, li)
    r = c.post("/api/ahb/leads/sync",
               json={"accounts": ["contactahbco@gmail.com"]})
    assert r.status_code == 200, r.get_data(as_text=True)
    j = r.get_json()
    assert j["leads_new"] == 1 and j["reviews_new"] == 1


# --- Task 4: List filter, detail, PATCH status/notes ---

def _seed_lead(li, **over):
    msg = {"gmail_id": over.pop("gmail_id", "g-seed"),
           "account_email": "contactahbco@gmail.com",
           "received_at": "2026-06-28T10:00:00Z"}
    parsed = {"kind": "lead", "customer_name": over.pop("customer_name", "Lead A"),
              "service_type": "Remodel", "contact_email": over.pop("email", "a@x.com")}
    return li._upsert_lead(over.pop("platform", "thumbtack"), msg, parsed)


def test_lead_detail_and_filter(env):
    c, li, db = env
    lid = _seed_lead(li)
    r = c.get(f"/api/ahb/leads/{lid}")
    assert r.status_code == 200
    assert r.get_json()["customer_name"] == "Lead A"
    # filter
    assert len(c.get("/api/ahb/leads?status=new").get_json()["items"]) == 1
    assert c.get("/api/ahb/leads?status=won").get_json()["items"] == []


def test_lead_patch_status(env):
    c, li, db = env
    lid = _seed_lead(li)
    r = c.patch(f"/api/ahb/leads/{lid}",
                json={"status": "contacted", "notes": "called"})
    assert r.status_code == 200
    con = sqlite3.connect(db)
    row = con.execute("SELECT status, notes FROM ahb_leads WHERE id=?",
                      (lid,)).fetchone()
    con.close()
    assert row == ("contacted", "called")


def test_lead_patch_rejects_unknown_field(env):
    c, li, db = env
    lid = _seed_lead(li)
    r = c.patch(f"/api/ahb/leads/{lid}", json={"budget": "hacked"})
    assert r.status_code == 400


# --- Task 5: Draft a reply (local LLM) ---

def test_lead_draft(env, monkeypatch):
    c, li, db = env
    lid = _seed_lead(li)
    monkeypatch.setattr(li, "_ollama_chat",
                        lambda model, system, user, **kw:
                        "Hi Lead A, thanks for reaching out to All Home Building!")
    r = c.post(f"/api/ahb/leads/{lid}/draft", json={})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert "All Home Building" in r.get_json()["draft"]
    con = sqlite3.connect(db)
    stored = con.execute("SELECT draft_reply FROM ahb_leads WHERE id=?",
                         (lid,)).fetchone()[0]
    con.close()
    assert "All Home Building" in stored


def test_lead_draft_missing_lead(env):
    c, li, db = env
    r = c.post("/api/ahb/leads/9999/draft", json={})
    assert r.status_code == 404


# --- Task 6: Convert lead → client (+ optional project) ---

def test_lead_convert_client_and_project(env):
    c, li, db = env
    lid = _seed_lead(li, customer_name="Convert Me", email="c@x.com")
    r = c.post(f"/api/ahb/leads/{lid}/convert", json={"create_project": True})
    assert r.status_code == 200, r.get_data(as_text=True)
    j = r.get_json()
    assert j["client_id"] and j["project_id"]
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    cl = con.execute("SELECT name, source, email FROM ahb_clients WHERE id=?",
                     (j["client_id"],)).fetchone()
    pr = con.execute("SELECT status, acquisition_type, client_id FROM ahb_projects "
                     "WHERE id=?", (j["project_id"],)).fetchone()
    lead = con.execute("SELECT status, converted_client_id FROM ahb_leads WHERE id=?",
                       (lid,)).fetchone()
    con.close()
    assert cl["name"] == "Convert Me" and cl["source"] == "thumbtack"
    assert pr["status"] == "Planning" and pr["acquisition_type"] == "lead"
    assert pr["client_id"] == j["client_id"]
    assert lead["status"] == "won" and lead["converted_client_id"] == j["client_id"]


def test_lead_convert_is_idempotent(env):
    c, li, db = env
    lid = _seed_lead(li)
    j1 = c.post(f"/api/ahb/leads/{lid}/convert", json={}).get_json()
    j2 = c.post(f"/api/ahb/leads/{lid}/convert", json={}).get_json()
    assert j1["client_id"] == j2["client_id"]
    con = sqlite3.connect(db)
    n = con.execute("SELECT COUNT(*) FROM ahb_clients").fetchone()[0]
    con.close()
    assert n == 1  # no duplicate client


def test_lead_convert_without_project(env):
    c, li, db = env
    lid = _seed_lead(li)
    j = c.post(f"/api/ahb/leads/{lid}/convert", json={"create_project": False}).get_json()
    assert j["client_id"] and j["project_id"] is None


# --- Task 7: External reviews listing ---

def test_reviews_external_list(env, monkeypatch):
    c, li, db = env
    _wire_sync(monkeypatch, li)
    li.sync(accounts=["contactahbco@gmail.com"])
    r = c.get("/api/ahb/reviews/external")
    assert r.status_code == 200
    items = r.get_json()["items"]
    assert len(items) == 1
    assert items[0]["platform"] == "angi"
    assert items[0]["reviewer_name"] == "Bob"
    # platform filter
    assert c.get("/api/ahb/reviews/external?platform=thumbtack").get_json()["items"] == []


# --- Task 8: Blueprint registration smoke test ---

def test_app_registers_lead_bp():
    import importlib
    sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))
    app_mod = importlib.import_module("app")
    rules = {r.rule for r in app_mod.app.url_map.iter_rules()}
    assert "/api/ahb/leads" in rules
    assert "/api/ahb/leads/sync" in rules
