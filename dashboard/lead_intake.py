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
import threading
import time
import urllib.request
import uuid
from typing import Optional

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

_TOKEN_REFRESH_LOCK = threading.Lock()


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
                converted_client_id TEXT,
                converted_project_id TEXT,
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
            -- Stubs mirroring app.py's authoritative ahb_clients/ahb_projects
            -- schema; IF NOT EXISTS makes this a no-op against the real tables.
            CREATE TABLE IF NOT EXISTS ahb_clients (
                id TEXT PRIMARY KEY,
                name TEXT,
                phone TEXT,
                email TEXT,
                address TEXT,
                city TEXT DEFAULT 'Philadelphia',
                source TEXT,
                status TEXT DEFAULT 'lead',
                notes TEXT,
                assigned_agent TEXT,
                company TEXT DEFAULT '',
                tags TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS ahb_projects (
                id TEXT PRIMARY KEY,
                client_id TEXT,
                title TEXT,
                scope TEXT,
                status TEXT DEFAULT 'Planning',
                acquisition_type TEXT DEFAULT '',
                client_name TEXT DEFAULT '',
                client_email TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
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


# --- Task 2: Upsert helpers + dedup + low-rating flag ---

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


# --- Task 3: boundaries + sync() + route ---

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
        with _TOKEN_REFRESH_LOCK:
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


def _send_email(account_email: str, to: str, subject: str, body: str) -> dict:
    """Send a plain-text email from account_email via Gmail. Boundary (monkeypatched in tests)."""
    import base64
    from email.mime.text import MIMEText
    svc = _gmail_service(account_email)
    msg = MIMEText(body or "", "plain", _charset="utf-8")
    msg["To"] = to
    msg["Subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return svc.users().messages().send(userId="me", body={"raw": raw}).execute()


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


# --- orchestration ---

def sync(accounts: Optional[list] = None, full: bool = False) -> dict:
    accounts = accounts or list(DEFAULT_ACCOUNTS)
    leads_new = reviews_new = 0
    errors: list = []
    for account in accounts:
        account_errors = []
        since = None if full else _get_cursor(account)
        for platform, senders in PLATFORM_SENDERS.items():
            try:
                messages = _gmail_search(account, senders, since)
            except Exception as e:
                account_errors.append(f"{account}/{platform}: {e}")
                continue
            for msg in messages:
                gid = msg.get("gmail_id")
                if not gid or _already_seen(platform, gid):
                    continue
                try:
                    parsed = _parse_email(platform, msg)
                except Exception as e:
                    account_errors.append(f"{account}/{platform}/{gid}: {e}")
                    continue
                kind = parsed.get("kind")
                if kind == "lead":
                    _upsert_lead(platform, msg, parsed)
                    leads_new += 1
                elif kind == "review":
                    _upsert_review(platform, msg, parsed)
                    reviews_new += 1
        errors.extend(account_errors)
        if not account_errors:
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


# --- Task 4: detail + PATCH ---

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


# --- Task 5: Draft reply (local LLM) ---

def _business_voice() -> str:
    """Short brand name for reply drafting, from ahb_business_profile if present."""
    con = _db()
    try:
        r = con.execute("SELECT * FROM ahb_business_profile LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        r = None
    finally:
        con.close()
    name = "All Home Building Co LLC"
    if r is not None and r.keys():
        if "dba" in r.keys() and r["dba"]:
            name = r["dba"]
        elif "legal_name" in r.keys() and r["legal_name"]:
            name = r["legal_name"]
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


# --- Task 9: Gated send-draft-reply ---

@lead_bp.route("/api/ahb/leads/<int:lid>/send-reply", methods=["POST"])
def lead_send_reply(lid):
    data = request.get_json(silent=True) or {}
    if not data.get("confirm"):
        return jsonify({"error": "confirm required — sending email is outward-facing"}), 400
    con = _db()
    try:
        r = con.execute("SELECT * FROM ahb_leads WHERE id=?", (lid,)).fetchone()
    finally:
        con.close()
    if not r:
        return jsonify({"error": "not found"}), 404
    to = (r["contact_email"] or "").strip()
    if not to:
        return jsonify({"error": "lead has no contact email — cannot send"}), 400
    if "@" not in to or " " in to:
        return jsonify({"error": "lead contact email looks malformed — fix it first"}), 400
    body = (data.get("body") or r["draft_reply"] or "").strip()
    if not body:
        return jsonify({"error": "no draft to send — draft a reply first"}), 400
    subject = (data.get("subject") or
               f"Re: your {r['service_type'] or 'project'} request — All Home Building Co LLC")
    try:
        _send_email(r["account_email"] or "", to, subject, body)
    except Exception as e:
        print(f"[lead_intake] send-reply failed for lead {lid}: {e}", flush=True)
        return jsonify({"error": "send failed — check the account's Gmail "
                                 "connection and try again"}), 502
    con = _db()
    try:
        con.execute("UPDATE ahb_leads SET status='contacted', updated_at=CURRENT_TIMESTAMP "
                    "WHERE id=?", (lid,))
        con.commit()
    finally:
        con.close()
    return jsonify({"ok": True, "to": to})


# --- Task 6: Convert lead → client (+ optional project) ---

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
        client_id = uuid.uuid4().hex[:24]
        con.execute(
            "INSERT INTO ahb_clients (id, name, phone, email, source, status, notes) "
            "VALUES (?,?,?,?,?,'active',?)",
            (client_id, lead["customer_name"], lead["contact_phone"], lead["contact_email"],
             lead["platform"], lead["details"]))
        project_id = None
        if create_project:
            title = " — ".join(x for x in (lead["service_type"],
                                           lead["customer_name"]) if x) or "New project"
            project_id = uuid.uuid4().hex[:24]
            con.execute(
                "INSERT INTO ahb_projects (id, client_id, title, scope, status, "
                "acquisition_type, client_name, client_email) "
                "VALUES (?,?,?,?,'Planning','lead',?,?)",
                (project_id, client_id, title, lead["details"], lead["customer_name"],
                 lead["contact_email"]))
        con.execute(
            "UPDATE ahb_leads SET converted_client_id=?, converted_project_id=?, "
            "status='won', updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (client_id, project_id, lid))
        con.commit()
    finally:
        con.close()
    return jsonify({"ok": True, "client_id": client_id, "project_id": project_id})


# --- Task 7: External reviews listing ---

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
