"""Tests for the unified-inbox feature (Tasks 1-3).

Tasks 4 and 5 are pure frontend (JS/HTML) and have no automated tests.
"""
import sqlite3
import sys
import os

import pytest

DASHBOARD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, DASHBOARD_DIR)

import email_studio


# ── Task 1: _hydrate_thread helper ────────────────────────────────────────

def test_hydrate_thread_stamps_account(monkeypatch):
    # Cached row present -> uses cache, stamps account fields
    class FakeCon:
        def execute(self, *a, **k):
            class R:
                def fetchone(self_inner): return None  # force remote path
            return R()
    class FakeSvc:
        def users(self): return self
        def threads(self): return self
        def get(self, **k):
            class E:
                def execute(self_inner):
                    return {"messages": [{"labelIds": ["INBOX"],
                            "payload": {"headers": [
                                {"name": "From", "value": "a@b.com"},
                                {"name": "Subject", "value": "Hi"},
                                {"name": "Date", "value": "2026-06-16"}]}}]}
            return E()
    t = {"id": "T1", "snippet": "snip"}
    out = email_studio._hydrate_thread(FakeSvc(), FakeCon(), t, "acc-1", "me@x.com")
    assert out["thread_id"] == "T1"
    assert out["account_id"] == "acc-1"
    assert out["account_email"] == "me@x.com"


# ── Task 2: api_threads with account=ALL ─────────────────────────────────

def test_threads_all_merges_and_sorts(monkeypatch, client):
    # Two accounts; each returns one thread. Merged result is sorted desc by received_at.
    accounts = [{"id": "a1", "email": "one@x.com"}, {"id": "a2", "email": "two@x.com"}]
    monkeypatch.setattr(email_studio, "_all_accounts", lambda: accounts)
    def fake_gmail(aid=None):
        class Svc:
            def users(self): return self
            def threads(self): return self
            def list(self, **k):
                class E:
                    def execute(self_inner):
                        return {"threads": [{"id": "t-" + aid, "snippet": "s"}]}
                return E()
        return Svc()
    monkeypatch.setattr(email_studio, "_gmail", fake_gmail)
    monkeypatch.setattr(email_studio, "_hydrate_thread",
        lambda svc, con, t, aid, ae: {"thread_id": t["id"], "account_id": aid,
            "account_email": ae, "received_at": "2026-06-1" + ("5" if aid == "a1" else "6")})
    r = client.get("/api/email2/threads?account=ALL&limit=10")
    data = r.get_json()
    ids = [t["thread_id"] for t in data["threads"]]
    assert ids == ["t-a2", "t-a1"]  # a2 newer -> first
    assert {t["account_id"] for t in data["threads"]} == {"a1", "a2"}


def test_threads_all_skips_failing_account(monkeypatch, client):
    accounts = [{"id": "a1", "email": "one@x.com"}, {"id": "a2", "email": "two@x.com"}]
    monkeypatch.setattr(email_studio, "_all_accounts", lambda: accounts)
    def fake_gmail(aid=None):
        if aid == "a1":
            raise RuntimeError("bad token")
        class Svc:
            def users(self): return self
            def threads(self): return self
            def list(self, **k):
                class E:
                    def execute(self_inner): return {"threads": [{"id": "t-a2"}]}
                return E()
        return Svc()
    monkeypatch.setattr(email_studio, "_gmail", fake_gmail)
    monkeypatch.setattr(email_studio, "_hydrate_thread",
        lambda svc, con, t, aid, ae: {"thread_id": t["id"], "account_id": aid, "received_at": "x"})
    r = client.get("/api/email2/threads?account=ALL&limit=10")
    assert [t["thread_id"] for t in r.get_json()["threads"]] == ["t-a2"]


# ── Task 3: api_search — account scoping + account_id in results ──────────

@pytest.fixture
def seed_emails(tmp_db):
    """Insert rows: gmail_id g1 (acc a1), g2 (acc a2), g3 (acc NULL) with 'invoice' content."""
    con = sqlite3.connect(tmp_db)
    con.execute("DELETE FROM emails")
    con.execute("DELETE FROM emails_fts")
    con.execute("DELETE FROM email_accounts")
    # Insert accounts
    con.execute("INSERT INTO email_accounts (id, email, token_path) VALUES ('a1','one@x.com','x')")
    con.execute("INSERT INTO email_accounts (id, email, token_path) VALUES ('a2','two@x.com','x')")
    # Insert emails
    con.execute("""INSERT INTO emails (id, gmail_id, thread_id, subject, from_addr, body_snippet, full_body, account_id)
                   VALUES ('e1', 'g1', 'thr1', 'Invoice A', 'sender@x.com', 'invoice details', 'invoice details', 'a1')""")
    con.execute("""INSERT INTO emails (id, gmail_id, thread_id, subject, from_addr, body_snippet, full_body, account_id)
                   VALUES ('e2', 'g2', 'thr2', 'Invoice B', 'sender@y.com', 'invoice details', 'invoice details', 'a2')""")
    con.execute("""INSERT INTO emails (id, gmail_id, thread_id, subject, from_addr, body_snippet, full_body, account_id)
                   VALUES ('e3', 'g3', 'thr3', 'Invoice C', 'sender@z.com', 'invoice details', 'invoice details', NULL)""")
    # Populate FTS
    con.execute("""INSERT INTO emails_fts (gmail_id, subject, from_addr, body)
                   VALUES ('g1', 'Invoice A', 'sender@x.com', 'invoice details')""")
    con.execute("""INSERT INTO emails_fts (gmail_id, subject, from_addr, body)
                   VALUES ('g2', 'Invoice B', 'sender@y.com', 'invoice details')""")
    con.execute("""INSERT INTO emails_fts (gmail_id, subject, from_addr, body)
                   VALUES ('g3', 'Invoice C', 'sender@z.com', 'invoice details')""")
    con.commit()
    con.close()
    yield tmp_db


def test_search_scopes_to_account_and_returns_account_id(client, seed_emails):
    # seed_emails fixture inserts rows: gmail_id g1(acc a1), g2(acc a2), g3(acc NULL)
    r_all = client.get("/api/email2/search?q=invoice&account=ALL").get_json()
    accs = {row["account_id"] for row in r_all["results"]}
    assert "a1" in accs and "a2" in accs
    r_one = client.get("/api/email2/search?q=invoice&account=a1").get_json()
    got = {row["account_id"] for row in r_one["results"]}
    assert got <= {"a1", None}  # a1 + legacy NULL only, never a2
