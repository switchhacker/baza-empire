# Thumbtack + Angi Lead & Review Intake (Track B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pull Thumbtack & Angi/HomeAdvisor leads and reviews out of Gmail with a local LLM into the AHB123 dashboard — a new Leads tab (pipeline + draft-reply + convert-to-client) and the existing Reviews tab extended to list external-platform reviews.

**Architecture:** A new `dashboard/lead_intake.py` module owns a Flask blueprint (`lead_bp`), two new SQLite tables (`ahb_leads`, `ahb_reviews`) plus a cursor table, and boundary helpers for Gmail (`_gmail_search`, reusing per-account tokens from `email_accounts`) and the local Ollama parser/drafter (`_ollama_chat`). All network + LLM calls sit behind monkeypatchable functions so the suite runs with no network and no LLM. Frontend adds a Leads tab and extends the Reviews tab in `templates/ahb123.html`.

**Tech Stack:** Python 3 / Flask, SQLite (`baza_projects.db`), Google API client (Gmail read, existing OAuth tokens), local Ollama via `urllib` (`127.0.0.1:11434/api/chat`), pytest. Vanilla JS in the dashboard template.

---

## Conventions for this plan

- **Commits:** Repo is auto-committed hourly by `claw-auto-git` (CLAUDE.md). **Do NOT `git commit` manually.** Each task's checkpoint is a green test run.
- **Run from repo root** `/home/switchhacker/baza-empire/agent-framework-v3` with `venv/bin/python -m pytest …`.
- **Test isolation:** new tests build a Flask app, register `lead_bp`, and point `BAZA_DASHBOARD_DB` at a tmp file — mirroring `tests/test_social_connect.py`'s `env` fixture. All `_gmail_search`, `_parse_email`, and `_ollama_chat` boundaries are monkeypatched. No network, no LLM, no Gmail.
- **Local-first:** parsing and drafting use local Ollama only; Gmail uses existing OAuth tokens. No cloud API.
- **Dashboard restart:** after editing `templates/ahb123.html`, `sudo systemctl restart baza-dashboard` (Jinja cache) — Task 9.

## File structure

| File | Create/Modify | Responsibility |
|------|---------------|----------------|
| `dashboard/lead_intake.py` | Create | Tables, Gmail/LLM boundaries, `sync`, `lead_bp` routes (sync/list/detail/patch/draft/convert/reviews) |
| `dashboard/app.py` | Modify | Register `lead_bp` |
| `dashboard/templates/ahb123.html` | Modify | New Leads tab + Reviews-tab external sources |
| `scripts/lead_intake_run.py` | Create | Timer entrypoint calling `sync` |
| `tests/test_lead_intake.py` | Create | Full backend TDD, all boundaries monkeypatched |

---

### Task 1: Module skeleton — tables + blueprint + empty list

**Files:**
- Create: `dashboard/lead_intake.py`
- Test: `tests/test_lead_intake.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_lead_intake.py`:

```python
"""Tests for Thumbtack/Angi lead + review intake.

Gmail and local-LLM boundaries are monkeypatched; no network, no LLM.
"""
import json
import os
import sqlite3
import sys
import time

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_lead_intake.py -v`
Expected: FAIL (`No module named 'lead_intake'`).

- [ ] **Step 3: Implement the module skeleton**

Create `dashboard/lead_intake.py`:

```python
"""Thumbtack + Angi/HomeAdvisor lead & review intake.

Parses platform notification emails out of Gmail with a LOCAL Ollama model into
ahb_leads / ahb_reviews. Local-first: Gmail read uses existing OAuth tokens;
all classification/extraction/drafting runs on local Ollama. No cloud API.

Network (Gmail) and LLM calls live behind module-level helpers
(`_gmail_search`, `_parse_email`, `_ollama_chat`) so tests monkeypatch them.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.request
from typing import Any, Optional

from flask import Blueprint, jsonify, request

DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))
FRAMEWORK_DIR = os.path.dirname(DASHBOARD_DIR)

# Gmail accounts that receive lead/review mail (verified 2026-06-28).
DEFAULT_ACCOUNTS = ("contactahbco@gmail.com", "sergek729@gmail.com")
PLATFORM_SENDERS = {
    "thumbtack": ["thumbtack.com", "mail.thumbtack.com"],
    "angi": ["angi.com", "homeadvisor.com", "leads.angi.com", "email.angi.com"],
}
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]
OLLAMA_BASE = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
LEAD_PARSE_MODEL = os.environ.get("LEAD_PARSE_MODEL", "gpt-oss:20b")

lead_bp = Blueprint("lead", __name__)


def _db_path() -> str:
    return os.environ.get(
        "BAZA_DASHBOARD_DB", os.path.join(DASHBOARD_DIR, "baza_projects.db"))


def _db():
    con = sqlite3.connect(_db_path(), timeout=8.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 8000")
    return con


def _ensure_tables(db_path: Optional[str] = None) -> None:
    con = None
    try:
        con = sqlite3.connect(db_path or _db_path(), timeout=8.0)
        con.execute("PRAGMA busy_timeout = 8000")
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS ahb_leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL,
                platform_lead_id TEXT,
                customer_name TEXT,
                service_type TEXT,
                location TEXT,
                zip TEXT,
                budget TEXT,
                details TEXT,
                contact_phone TEXT,
                contact_email TEXT,
                status TEXT DEFAULT 'new',
                draft_reply TEXT,
                gmail_id TEXT,
                account_email TEXT,
                received_at TEXT,
                converted_client_id INTEGER,
                converted_project_id INTEGER,
                notes TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(platform, gmail_id)
            );
            CREATE TABLE IF NOT EXISTS ahb_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL,
                reviewer_name TEXT,
                rating REAL,
                review_text TEXT,
                review_date TEXT,
                source_url TEXT,
                responded INTEGER DEFAULT 0,
                flagged_low INTEGER DEFAULT 0,
                gmail_id TEXT,
                account_email TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(platform, gmail_id)
            );
            CREATE TABLE IF NOT EXISTS lead_intake_state (
                account_email TEXT PRIMARY KEY,
                last_synced_epoch INTEGER
            );
            """
        )
        con.commit()
    finally:
        if con is not None:
            con.close()


def _row_to_lead(r: sqlite3.Row) -> dict:
    d = dict(r)
    return d


@lead_bp.route("/api/ahb/leads", methods=["GET"])
def leads_list():
    status = (request.args.get("status") or "").strip()
    con = _db()
    try:
        if status and status != "all":
            rows = con.execute(
                "SELECT * FROM ahb_leads WHERE status=? ORDER BY received_at DESC, id DESC",
                (status,)).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM ahb_leads ORDER BY received_at DESC, id DESC").fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        con.close()
    return jsonify({"items": [_row_to_lead(r) for r in rows]})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_lead_intake.py -v`
Expected: PASS (both).

- [ ] **Step 5: Checkpoint**

Run: `venv/bin/python -m pytest tests/test_lead_intake.py -q`
Expected: 2 passed.

---

### Task 2: Upsert helpers + dedup + low-rating flag

**Files:**
- Modify: `dashboard/lead_intake.py`
- Test: `tests/test_lead_intake.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_lead_intake.py -k "upsert or already_seen" -v`
Expected: FAIL (`_upsert_lead` not defined).

- [ ] **Step 3: Implement the helpers**

Append to `dashboard/lead_intake.py` (after `_row_to_lead`):

```python
def _upsert_lead(platform: str, msg: dict, parsed: dict) -> int:
    con = _db()
    try:
        existing = con.execute(
            "SELECT id FROM ahb_leads WHERE platform=? AND gmail_id=?",
            (platform, msg.get("gmail_id"))).fetchone()
        if existing:
            return existing["id"]
        cur = con.execute(
            "INSERT INTO ahb_leads (platform, platform_lead_id, customer_name, "
            "service_type, location, zip, budget, details, contact_phone, "
            "contact_email, gmail_id, account_email, received_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (platform, parsed.get("platform_lead_id"), parsed.get("customer_name"),
             parsed.get("service_type"), parsed.get("location"), parsed.get("zip"),
             parsed.get("budget"), parsed.get("details"), parsed.get("contact_phone"),
             parsed.get("contact_email"), msg.get("gmail_id"),
             msg.get("account_email"), msg.get("received_at")))
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def _upsert_review(platform: str, msg: dict, parsed: dict) -> int:
    con = _db()
    try:
        existing = con.execute(
            "SELECT id FROM ahb_reviews WHERE platform=? AND gmail_id=?",
            (platform, msg.get("gmail_id"))).fetchone()
        if existing:
            return existing["id"]
        try:
            rating = float(parsed.get("rating")) if parsed.get("rating") is not None else None
        except (TypeError, ValueError):
            rating = None
        flagged = 1 if (rating is not None and rating <= 3) else 0
        cur = con.execute(
            "INSERT INTO ahb_reviews (platform, reviewer_name, rating, review_text, "
            "review_date, source_url, flagged_low, gmail_id, account_email) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (platform, parsed.get("reviewer_name"), rating, parsed.get("review_text"),
             parsed.get("review_date"), parsed.get("source_url"), flagged,
             msg.get("gmail_id"), msg.get("account_email")))
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def _already_seen(platform: str, gmail_id: str) -> bool:
    con = _db()
    try:
        a = con.execute("SELECT 1 FROM ahb_leads WHERE platform=? AND gmail_id=?",
                        (platform, gmail_id)).fetchone()
        b = con.execute("SELECT 1 FROM ahb_reviews WHERE platform=? AND gmail_id=?",
                        (platform, gmail_id)).fetchone()
        return bool(a or b)
    finally:
        con.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_lead_intake.py -k "upsert or already_seen" -v`
Expected: PASS (all three).

- [ ] **Step 5: Checkpoint**

Run: `venv/bin/python -m pytest tests/test_lead_intake.py -q`
Expected: all pass.

---

### Task 3: `sync()` orchestration + `POST /api/ahb/leads/sync`

**Files:**
- Modify: `dashboard/lead_intake.py`
- Test: `tests/test_lead_intake.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
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


def test_sync_route(env, monkeypatch):
    c, li, db = env
    _wire_sync(monkeypatch, li)
    r = c.post("/api/ahb/leads/sync",
               json={"accounts": ["contactahbco@gmail.com"]})
    assert r.status_code == 200, r.get_data(as_text=True)
    j = r.get_json()
    assert j["leads_new"] == 1 and j["reviews_new"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_lead_intake.py -k "sync" -v`
Expected: FAIL (`sync` not defined).

- [ ] **Step 3: Implement `sync` + boundaries + route**

Append to `dashboard/lead_intake.py`:

```python
# --- boundaries (monkeypatched in tests) --------------------------------------
def _ollama_chat(model: str, system: str, user: str,
                 temperature: float = 0.2, timeout: int = 90) -> str:
    payload = {"model": model, "stream": False,
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": user}],
               "options": {"temperature": temperature}, "format": "json"}
    req = urllib.request.Request(
        f"{OLLAMA_BASE}/api/chat", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    return (data.get("message") or {}).get("content", "")


def _account_token_path(account_email: str) -> Optional[str]:
    con = _db()
    try:
        r = con.execute("SELECT token_path FROM email_accounts WHERE email=?",
                        (account_email,)).fetchone()
        return r["token_path"] if r else None
    except sqlite3.OperationalError:
        return None
    finally:
        con.close()


def _gmail_service(account_email: str):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request as GRequest
    import googleapiclient.discovery
    token_path = _account_token_path(account_email)
    if not token_path or not os.path.exists(token_path):
        raise RuntimeError(f"no Gmail token for {account_email} — run gmail_auth")
    creds = Credentials.from_authorized_user_file(token_path, GMAIL_SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(GRequest())
        with open(token_path, "w") as f:
            f.write(creds.to_json())
    return googleapiclient.discovery.build(
        "gmail", "v1", credentials=creds, static_discovery=True)


def _extract_body(payload: dict) -> str:
    """Recursively pull text/plain (fallback text/html) from a Gmail payload."""
    import base64
    def _decode(data):
        return base64.urlsafe_b64decode(data.encode()).decode("utf-8", "replace")
    mime = payload.get("mimeType", "")
    body = payload.get("body", {})
    if mime == "text/plain" and body.get("data"):
        return _decode(body["data"])
    out = []
    for part in payload.get("parts", []) or []:
        out.append(_extract_body(part))
    joined = "\n".join(p for p in out if p)
    if joined:
        return joined
    if mime == "text/html" and body.get("data"):
        return _decode(body["data"])
    return ""


def _gmail_search(account_email: str, senders: list, since_epoch: Optional[int]) -> list:
    svc = _gmail_service(account_email)
    q = "from:({})".format(" OR ".join(senders))
    if since_epoch:
        q += f" after:{int(since_epoch)}"
    out = []
    resp = svc.users().messages().list(userId="me", q=q, maxResults=50).execute()
    for m in resp.get("messages", []) or []:
        full = svc.users().messages().get(
            userId="me", id=m["id"], format="full").execute()
        headers = {h["name"].lower(): h["value"]
                   for h in full.get("payload", {}).get("headers", [])}
        out.append({"gmail_id": m["id"], "from_addr": headers.get("from", ""),
                    "subject": headers.get("subject", ""),
                    "received_at": headers.get("date", ""),
                    "account_email": account_email,
                    "body": _extract_body(full.get("payload", {}))})
    return out


def _parse_email(platform: str, msg: dict) -> dict:
    """Local-LLM classify + extract. Raises ValueError on unusable output."""
    system = (
        "You extract structured data from a home-services platform email. "
        "Return ONLY JSON. If it is a customer LEAD, return "
        '{"kind":"lead","customer_name","service_type","location","zip",'
        '"budget","details","contact_phone","contact_email"}. If it is a customer '
        'REVIEW, return {"kind":"review","reviewer_name","rating","review_text",'
        '"review_date","source_url"}. Otherwise return {"kind":"other"}. '
        "Use null for unknown fields.")
    user = f"Platform: {platform}\nSubject: {msg.get('subject','')}\n\n{msg.get('body','')}"
    raw = _ollama_chat(LEAD_PARSE_MODEL, system, user)
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as e:
        raise ValueError(f"unparseable LLM output: {e}")
    if not isinstance(data, dict) or "kind" not in data:
        raise ValueError("LLM output missing 'kind'")
    return data


# --- orchestration ------------------------------------------------------------
def sync(accounts: Optional[list] = None, full: bool = False) -> dict:
    accounts = accounts or list(DEFAULT_ACCOUNTS)
    leads_new = reviews_new = 0
    errors: list = []
    for account in accounts:
        since = None if full else _get_cursor(account)
        for platform, senders in PLATFORM_SENDERS.items():
            try:
                messages = _gmail_search(account, senders, since)
            except Exception as e:
                errors.append(f"{account}/{platform}: {e}")
                continue
            for msg in messages:
                gid = msg.get("gmail_id")
                if not gid or _already_seen(platform, gid):
                    continue
                try:
                    parsed = _parse_email(platform, msg)
                except Exception as e:
                    errors.append(f"{account}/{platform}/{gid}: {e}")
                    continue
                kind = parsed.get("kind")
                if kind == "lead":
                    _upsert_lead(platform, msg, parsed)
                    leads_new += 1
                elif kind == "review":
                    _upsert_review(platform, msg, parsed)
                    reviews_new += 1
        _set_cursor(account, int(time.time()))
    return {"leads_new": leads_new, "reviews_new": reviews_new, "errors": errors}


def _get_cursor(account_email: str) -> Optional[int]:
    con = _db()
    try:
        r = con.execute("SELECT last_synced_epoch FROM lead_intake_state "
                        "WHERE account_email=?", (account_email,)).fetchone()
        return r["last_synced_epoch"] if r else None
    finally:
        con.close()


def _set_cursor(account_email: str, epoch: int) -> None:
    con = _db()
    try:
        con.execute(
            "INSERT INTO lead_intake_state (account_email, last_synced_epoch) "
            "VALUES (?,?) ON CONFLICT(account_email) DO UPDATE SET last_synced_epoch=?",
            (account_email, epoch, epoch))
        con.commit()
    finally:
        con.close()


@lead_bp.route("/api/ahb/leads/sync", methods=["POST"])
def leads_sync():
    data = request.get_json(silent=True) or {}
    accounts = data.get("accounts") or None
    full = bool(data.get("full"))
    res = sync(accounts=accounts, full=full)
    return jsonify(res)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_lead_intake.py -k "sync" -v`
Expected: PASS (all five).

- [ ] **Step 5: Checkpoint**

Run: `venv/bin/python -m pytest tests/test_lead_intake.py -q`
Expected: all pass.

---

### Task 4: List filter, detail, PATCH status/notes

**Files:**
- Modify: `dashboard/lead_intake.py`
- Test: `tests/test_lead_intake.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_lead_intake.py -k "detail or patch" -v`
Expected: FAIL (404 — routes missing).

- [ ] **Step 3: Implement the routes**

Append to `dashboard/lead_intake.py`:

```python
@lead_bp.route("/api/ahb/leads/<int:lid>", methods=["GET"])
def lead_detail(lid):
    con = _db()
    try:
        r = con.execute("SELECT * FROM ahb_leads WHERE id=?", (lid,)).fetchone()
    finally:
        con.close()
    if not r:
        return jsonify({"error": "not found"}), 404
    return jsonify(_row_to_lead(r))


_LEAD_PATCH_FIELDS = {"status", "notes"}


@lead_bp.route("/api/ahb/leads/<int:lid>", methods=["PATCH"])
def lead_patch(lid):
    data = request.get_json(silent=True) or {}
    fields = {k: v for k, v in data.items() if k in _LEAD_PATCH_FIELDS}
    if not fields:
        return jsonify({"error": "no updatable fields (allowed: status, notes)"}), 400
    sets = ", ".join(f"{k}=?" for k in fields)
    con = _db()
    try:
        cur = con.execute(
            f"UPDATE ahb_leads SET {sets}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (*fields.values(), lid))
        con.commit()
        if cur.rowcount == 0:
            return jsonify({"error": "not found"}), 404
    finally:
        con.close()
    return jsonify({"ok": True})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_lead_intake.py -k "detail or patch" -v`
Expected: PASS (all three).

- [ ] **Step 5: Checkpoint**

Run: `venv/bin/python -m pytest tests/test_lead_intake.py -q`
Expected: all pass.

---

### Task 5: Draft a reply (local LLM)

**Files:**
- Modify: `dashboard/lead_intake.py`
- Test: `tests/test_lead_intake.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_lead_intake.py -k "draft" -v`
Expected: FAIL (404 — route missing).

- [ ] **Step 3: Implement the draft route**

Append to `dashboard/lead_intake.py`:

```python
def _business_voice() -> str:
    """Short brand context for reply drafting, from ahb_business_profile if present."""
    con = _db()
    try:
        r = con.execute("SELECT * FROM ahb_business_profile LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        r = None
    finally:
        con.close()
    name = "All Home Building Co LLC"
    if r and "name" in r.keys() and r["name"]:
        name = r["name"]
    return name


@lead_bp.route("/api/ahb/leads/<int:lid>/draft", methods=["POST"])
def lead_draft(lid):
    con = _db()
    try:
        r = con.execute("SELECT * FROM ahb_leads WHERE id=?", (lid,)).fetchone()
    finally:
        con.close()
    if not r:
        return jsonify({"error": "not found"}), 404
    biz = _business_voice()
    system = (f"You are a friendly, professional estimator for {biz}, a home "
              "building & remodeling company. Draft a SHORT first reply to a new "
              "lead: thank them, confirm the service, and propose a quick call or "
              "site visit to give an estimate. Plain text, no placeholders.")
    user = (f"Customer: {r['customer_name']}\nService: {r['service_type']}\n"
            f"Location: {r['location']}\nDetails: {r['details']}")
    try:
        draft = _ollama_chat(LEAD_PARSE_MODEL, system, user, temperature=0.5)
    except Exception as e:
        return jsonify({"error": f"draft failed: {e}"}), 502
    con = _db()
    try:
        con.execute("UPDATE ahb_leads SET draft_reply=?, updated_at=CURRENT_TIMESTAMP "
                    "WHERE id=?", (draft, lid))
        con.commit()
    finally:
        con.close()
    return jsonify({"ok": True, "draft": draft})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_lead_intake.py -k "draft" -v`
Expected: PASS (both).

- [ ] **Step 5: Checkpoint**

Run: `venv/bin/python -m pytest tests/test_lead_intake.py -q`
Expected: all pass.

---

### Task 6: Convert lead → client (+ optional project)

**Files:**
- Modify: `dashboard/lead_intake.py`
- Test: `tests/test_lead_intake.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_lead_intake.py -k "convert" -v`
Expected: FAIL (404 — route missing).

- [ ] **Step 3: Implement the convert route**

Append to `dashboard/lead_intake.py`:

```python
@lead_bp.route("/api/ahb/leads/<int:lid>/convert", methods=["POST"])
def lead_convert(lid):
    data = request.get_json(silent=True) or {}
    create_project = bool(data.get("create_project", True))
    con = _db()
    try:
        lead = con.execute("SELECT * FROM ahb_leads WHERE id=?", (lid,)).fetchone()
        if not lead:
            return jsonify({"error": "not found"}), 404
        if lead["converted_client_id"]:  # idempotent
            return jsonify({"ok": True, "client_id": lead["converted_client_id"],
                            "project_id": lead["converted_project_id"]})
        cur = con.execute(
            "INSERT INTO ahb_clients (name, phone, email, source, status, notes) "
            "VALUES (?,?,?,?,'active',?)",
            (lead["customer_name"], lead["contact_phone"], lead["contact_email"],
             lead["platform"], lead["details"]))
        client_id = cur.lastrowid
        project_id = None
        if create_project:
            title = " — ".join(x for x in (lead["service_type"],
                                           lead["customer_name"]) if x) or "New project"
            pc = con.execute(
                "INSERT INTO ahb_projects (client_id, title, scope, status, "
                "acquisition_type, client_name, client_email) "
                "VALUES (?,?,?,'Planning','lead',?,?)",
                (client_id, title, lead["details"], lead["customer_name"],
                 lead["contact_email"]))
            project_id = pc.lastrowid
        con.execute(
            "UPDATE ahb_leads SET converted_client_id=?, converted_project_id=?, "
            "status='won', updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (client_id, project_id, lid))
        con.commit()
    finally:
        con.close()
    return jsonify({"ok": True, "client_id": client_id, "project_id": project_id})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_lead_intake.py -k "convert" -v`
Expected: PASS (all three).

- [ ] **Step 5: Checkpoint**

Run: `venv/bin/python -m pytest tests/test_lead_intake.py -q`
Expected: all pass.

---

### Task 7: External reviews listing

**Files:**
- Modify: `dashboard/lead_intake.py`
- Test: `tests/test_lead_intake.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_lead_intake.py -k "reviews_external" -v`
Expected: FAIL (404 — route missing).

- [ ] **Step 3: Implement the route**

Append to `dashboard/lead_intake.py`:

```python
@lead_bp.route("/api/ahb/reviews/external", methods=["GET"])
def reviews_external():
    platform = (request.args.get("platform") or "").strip()
    con = _db()
    try:
        if platform and platform != "all":
            rows = con.execute(
                "SELECT * FROM ahb_reviews WHERE platform=? "
                "ORDER BY review_date DESC, id DESC", (platform,)).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM ahb_reviews ORDER BY review_date DESC, id DESC").fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        con.close()
    return jsonify({"items": [dict(r) for r in rows]})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_lead_intake.py -k "reviews_external" -v`
Expected: PASS.

- [ ] **Step 5: Checkpoint — full module suite**

Run: `venv/bin/python -m pytest tests/test_lead_intake.py -q`
Expected: all pass (~18 tests).

---

### Task 8: Register the blueprint in the dashboard app

**Files:**
- Modify: `dashboard/app.py` (near the other `register_blueprint` calls, ~line 15904-15922)
- Test: import smoke

- [ ] **Step 1: Write the failing test**

Append to `tests/test_lead_intake.py`:

```python
def test_app_registers_lead_bp():
    import importlib
    sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))
    app_mod = importlib.import_module("app")
    rules = {r.rule for r in app_mod.app.url_map.iter_rules()}
    assert "/api/ahb/leads" in rules
    assert "/api/ahb/leads/sync" in rules
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_lead_intake.py -k "registers_lead_bp" -v`
Expected: FAIL (route not registered).

- [ ] **Step 3: Register the blueprint**

In `dashboard/app.py`, find the block that imports + registers `_social_bp` (around line 15904-15908). Immediately after `app.register_blueprint(_social_bp)`, add:

```python
try:
    from dashboard.lead_intake import lead_bp as _lead_bp, _ensure_tables as _ensure_lead_tables
except ImportError:
    from lead_intake import lead_bp as _lead_bp, _ensure_tables as _ensure_lead_tables
_ensure_lead_tables()
app.register_blueprint(_lead_bp)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_lead_intake.py -k "registers_lead_bp" -v`
Expected: PASS.

- [ ] **Step 5: Checkpoint — whole module suite + import**

Run: `venv/bin/python -m pytest tests/test_lead_intake.py -q`
Expected: all pass.

---

### Task 9: Frontend — Leads tab + Reviews-tab external sources

**Files:**
- Modify: `dashboard/templates/ahb123.html`
- Verify: manual (Task 10)

This task adds a Leads tab and extends the Reviews list. Locate insertion points by anchor text (the template is large). Follow the existing tab-module pattern (e.g. how `reviews:` is wired into the tab-loader map near `reviews: loadReviews`).

- [ ] **Step 1: Add the Leads sub-tab nav entry**

Find the Reviews nav entry: `<div class="sub-tab" data-tab="reviews" onclick="switchTab('reviews')"><span class="sub-tab-icon">⭐</span> Reviews</div>`. Immediately AFTER it, add:
```html
  <div class="sub-tab" data-tab="leads" onclick="switchTab('leads')"><span class="sub-tab-icon">🎯</span> Leads</div>
```

- [ ] **Step 2: Add the Leads tab pane**

Find the opening of the reviews pane: `<div class="tab-pane" id="tab-reviews">`. Immediately BEFORE that line, insert a new pane:
```html
<div class="tab-pane" id="tab-leads">
  <div class="page-header">
    <div>
      <div class="page-title">Leads</div>
      <div class="page-sub">Thumbtack &amp; Angi prospects — parsed from your inbox</div>
    </div>
    <button class="btn-primary" onclick="AhbLeads.sync()">⟳ Sync now</button>
  </div>
  <div id="leads-filter" style="display:flex;gap:6px;margin:10px 0;flex-wrap:wrap"></div>
  <div id="leads-list" style="display:flex;flex-direction:column;gap:10px">
    <div style="text-align:center;padding:20px;color:#333;font-size:12px">Loading leads…</div>
  </div>
</div>
```

- [ ] **Step 3: Register `leads` in the tab-loader map**

Find the loader map containing `reviews: loadReviews` (an object mapping tab keys to loader functions). Add an entry:
```javascript
    leads: () => AhbLeads.render(),
```
(Match the surrounding entries' syntax — if they are `key: fnName` without arrows, use `leads: AhbLeads.render` and ensure `AhbLeads` is defined before this map is used; the IIFE below is hoisted via `window.AhbLeads` assignment, so prefer the arrow form shown.)

- [ ] **Step 4: Add the `AhbLeads` JS module**

Immediately BEFORE the `loadReviews` function definition (find `function loadReviews`/`async function loadReviews`), add:
```javascript
const AhbLeads = (function(){
  const STATUSES = ['all','new','contacted','quoted','won','lost'];
  let filter = 'all';
  function _esc(s){ return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
  async function sync(){
    const btn = event && event.target; if (btn) btn.disabled = true;
    try {
      const r = await fetch('/api/ahb/leads/sync', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({})});
      const j = await r.json();
      alert(`Synced: ${j.leads_new||0} new lead(s), ${j.reviews_new||0} new review(s)` + ((j.errors&&j.errors.length)?`\n${j.errors.length} skipped`:''));
    } catch(e){ alert('Sync failed: '+e.message); }
    if (btn) btn.disabled = false;
    render();
  }
  async function render(){
    const root = document.getElementById('leads-list'); if (!root) return;
    document.getElementById('leads-filter').innerHTML = STATUSES.map(s =>
      `<button class="btn-secondary" style="font-size:11px;padding:4px 10px;${s===filter?'background:#2a2a4a':''}" onclick="AhbLeads.setFilter('${s}')">${s}</button>`).join('');
    root.innerHTML = '<div style="color:#666;padding:16px">Loading…</div>';
    let items = [];
    try { items = (await (await fetch('/api/ahb/leads?status='+filter)).json()).items || []; }
    catch(e){ root.innerHTML = '<div style="color:#a55">Could not load leads.</div>'; return; }
    if (!items.length){ root.innerHTML = '<div style="color:#555;padding:16px;font-size:12px">No leads yet. Click “Sync now”.</div>'; return; }
    root.innerHTML = items.map(l => `
      <div style="background:#0b0b16;border:1px solid #1a1a2e;border-radius:8px;padding:12px">
        <div style="display:flex;justify-content:space-between;gap:8px">
          <div style="font-weight:700;color:#eee">${_esc(l.customer_name||'Unknown')} <span style="font-size:10px;color:#888">· ${_esc(l.platform)}</span></div>
          <span style="font-size:10px;color:#9af">${_esc(l.status)}</span>
        </div>
        <div style="font-size:12px;color:#bbb;margin-top:4px">${_esc(l.service_type||'')} ${l.location?('· '+_esc(l.location)):''} ${l.budget?('· '+_esc(l.budget)):''}</div>
        <div style="font-size:11px;color:#888;margin-top:4px">${_esc((l.details||'').slice(0,160))}</div>
        <div style="display:flex;gap:6px;margin-top:8px;flex-wrap:wrap">
          <button class="btn-secondary" style="font-size:11px;padding:3px 8px" onclick="AhbLeads.draft(${l.id})">✍ Draft reply</button>
          <button class="btn-secondary" style="font-size:11px;padding:3px 8px" onclick="AhbLeads.convert(${l.id})">➕ Convert</button>
          <select onchange="AhbLeads.setStatus(${l.id}, this.value)" style="font-size:11px;background:#070712;color:#ccc;border:1px solid #2a2a4a;border-radius:5px">
            ${['new','contacted','quoted','won','lost'].map(s=>`<option ${s===l.status?'selected':''}>${s}</option>`).join('')}
          </select>
        </div>
        ${l.draft_reply?`<div style="margin-top:8px;background:#070712;border:1px solid #1a1a2e;border-radius:6px;padding:8px;font-size:11px;color:#cdd;white-space:pre-wrap">${_esc(l.draft_reply)}</div>`:''}
      </div>`).join('');
  }
  function setFilter(s){ filter = s; render(); }
  async function setStatus(id, status){
    await fetch('/api/ahb/leads/'+id, {method:'PATCH', headers:{'Content-Type':'application/json'}, body: JSON.stringify({status})});
    render();
  }
  async function draft(id){
    const r = await fetch('/api/ahb/leads/'+id+'/draft', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
    const j = await r.json();
    if (!r.ok){ alert(j.error||'Draft failed'); return; }
    render();
  }
  async function convert(id){
    if (!confirm('Convert this lead into a client + project?')) return;
    const r = await fetch('/api/ahb/leads/'+id+'/convert', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({create_project:true})});
    const j = await r.json();
    if (!r.ok){ alert(j.error||'Convert failed'); return; }
    alert('Converted ✓'); render();
  }
  return { render, sync, setFilter, setStatus, draft, convert };
})();
window.AhbLeads = AhbLeads;
```

- [ ] **Step 5: Extend the Reviews list with external sources**

Find `async function loadReviews` (or `function loadReviews`). After it fetches first-party reviews and before/after it renders `reviews-list`, append external reviews. Locate the line that fetches `'/api/reviews/all'`; after the first-party render completes, add a fetch of external reviews and append them. Concretely, at the end of `loadReviews`'s success path (after `reviews-list` is populated), insert:
```javascript
  try {
    const ext = (await (await fetch('/api/ahb/reviews/external')).json()).items || [];
    if (ext.length){
      const host = document.getElementById('reviews-list');
      const block = document.createElement('div');
      block.innerHTML = '<div style="margin:14px 0 6px;font-size:12px;color:#888;font-weight:700">External platform reviews</div>' +
        ext.map(rv => `<div style="background:#0b0b16;border:1px solid #1a1a2e;border-radius:8px;padding:10px;margin-bottom:8px${rv.flagged_low?';border-color:#a55':''}">
          <div style="display:flex;justify-content:space-between"><span style="font-weight:700;color:#eee">${(rv.reviewer_name||'Anonymous').replace(/</g,'&lt;')}</span>
          <span style="font-size:11px;color:#fb3">${'★'.repeat(Math.round(rv.rating||0))} <span style="color:#789">· ${(rv.platform||'').replace(/</g,'&lt;')}</span></span></div>
          <div style="font-size:12px;color:#bbb;margin-top:4px">${(rv.review_text||'').replace(/</g,'&lt;')}</div></div>`).join('');
      host.appendChild(block);
    }
  } catch(e){ /* external reviews are best-effort */ }
```

- [ ] **Step 6: Restart the dashboard**

Run: `sudo systemctl restart baza-dashboard`
Expected: returns 0 (done in Task 10 verification too; safe to run here).

---

### Task 10: Timer entrypoint + live smoke + session log

**Files:**
- Create: `scripts/lead_intake_run.py`
- No DB/template code; verification + systemd unit text + session log.

- [ ] **Step 1: Create the timer entrypoint**

Create `scripts/lead_intake_run.py`:
```python
#!/usr/bin/env python3
"""Run a lead/review intake sync. Wired to baza-lead-intake.timer."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "dashboard"))

from lead_intake import sync, _ensure_tables  # noqa: E402

if __name__ == "__main__":
    _ensure_tables()
    full = "--full" in sys.argv
    res = sync(full=full)
    print(f"[lead_intake] leads_new={res['leads_new']} "
          f"reviews_new={res['reviews_new']} errors={len(res['errors'])}")
    for e in res["errors"]:
        print(f"[lead_intake]   skip: {e}")
```

- [ ] **Step 2: Full backend suite**

Run: `venv/bin/python -m pytest tests/test_lead_intake.py -q`
Expected: all pass (~19 tests).

- [ ] **Step 3: Restart dashboard + live route smoke**

Run:
```bash
sudo systemctl restart baza-dashboard && sleep 2 && systemctl is-active baza-dashboard
curl -s localhost:8888/api/ahb/leads | python3 -c "import sys,json;print('items' in json.load(sys.stdin))"
curl -s localhost:8888/api/ahb/reviews/external | python3 -c "import sys,json;print('items' in json.load(sys.stdin))"
```
Expected: `active`, then `True`, `True`.

- [ ] **Step 4: One-shot live intake (real Gmail; may surface token issues)**

Run: `venv/bin/python scripts/lead_intake_run.py --full`
Expected: prints a summary line. If `sergek729@gmail.com` errors with a token problem, that's expected — note it for Serge to re-auth via `email-pipeline/gmail_auth.py`; `contactahbco@` should succeed.

- [ ] **Step 5: Provide the systemd unit text (Serge installs)**

The timer + service are user-installed (don't auto-write to `/etc/systemd`). Record this text in the session log / hand to Serge:
```ini
# /etc/systemd/system/baza-lead-intake.service
[Unit]
Description=Baza lead/review intake (Thumbtack + Angi email parse)
[Service]
Type=oneshot
User=switchhacker
WorkingDirectory=/home/switchhacker/baza-empire/agent-framework-v3
ExecStart=/home/switchhacker/baza-empire/agent-framework-v3/venv/bin/python scripts/lead_intake_run.py

# /etc/systemd/system/baza-lead-intake.timer
[Unit]
Description=Run baza lead intake every 30 min
[Timer]
OnBootSec=5min
OnUnitActiveSec=30min
Persistent=true
[Install]
WantedBy=timers.target
```
Then: `sudo systemctl daemon-reload && sudo systemctl enable --now baza-lead-intake.timer`.

- [ ] **Step 6: Append session-log entry**

Run (timestamp from `date`):
```bash
printf '\n### %s | Lead/Review intake (Track B) shipped\n- dashboard/lead_intake.py: Gmail-search by sender (thumbtack/angi/homeadvisor) across contactahbco@+sergek729@, local-Ollama parse -> ahb_leads/ahb_reviews (dedup, low-rating flag, cursor). Routes leads sync/list/detail/patch/draft/convert + reviews/external; lead_bp registered in app.py. Leads tab + Reviews-tab external sources in ahb123.html. scripts/lead_intake_run.py + baza-lead-intake.timer (Serge installs unit). tests/test_lead_intake.py all green; dashboard restarted; live smoke OK. sergek729@ may need gmail re-auth. Next: Track C (profile-link directory).\n' "$(date '+%Y-%m-%d %H:%M')" >> ~/Desktop/baza-session-log.md
```

---

## Self-review notes (author)

- **Spec coverage:** §4.1 module/boundaries → T1-T3; §4.2 tables → T1; §4.3 routes (sync T3, list/detail/patch T4, draft T5, convert T6, reviews/external T7) → covered; §4.4 automation → T10; §4.5 frontend (Leads tab T9 s1-4, Reviews extension T9 s5) → covered; §6 tests → T1-T8; §7 partner-API → explicitly deferred (no task, by design); blueprint registration → T8. ✓
- **No placeholders:** every code/test step is complete and runnable.
- **Type/name consistency:** `_gmail_search(account, senders, since)`, `_parse_email(platform, msg)`, `_ollama_chat(model, system, user, **kw)`, `_upsert_lead/_upsert_review/_already_seen`, `sync(accounts, full)`, `_get_cursor/_set_cursor` — defined in T1-T3 and used identically in later tasks and tests. DB columns match the `_ensure_tables` schema (T1) throughout. `lead_bp` defined T1, registered T8.
- **Parse-failure semantics:** unparseable emails are skipped + recorded in `errors` (not row-stored), so they retry on the next run — this realizes the spec's "kept for retry" intent without an extra table or infinite-reprocess of stored rows (dedup only suppresses successfully-stored messages). Acceptable given low email volume.
- **Known follow-ups (out of v1):** partner-API `LeadSource` adapter (own spec), email-send of drafts (UI shows draft + copy; send wiring deferred), historical review backfill depth (bounded by Gmail `maxResults=50` per query per run — multiple runs walk further; note if deeper backfill is needed).
