"""Email Studio Blueprint — modern AI-infused Gmail UI.

Mounts under /api/email2/*. Owns:
- Schema migration on the existing `emails` table (extra columns + FTS5)
- Gmail API integration (list labels/threads/messages, send, modify)
- AI helpers (summarize / draft / polish / extract / categorize) on local Ollama
- Search (FTS5 keyword + optional semantic rerank)

Reuses the OAuth token at email-pipeline/token.json (read/send/modify scopes).
DB: dashboard/baza_projects.db, table `emails` (already populated by fetch_emails.py).
"""
from __future__ import annotations

import base64
import json
import os
import re
import sqlite3
import time
import urllib.request
import uuid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parseaddr
from typing import Any, Iterable, Optional

from flask import Blueprint, jsonify, request

DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))
FRAMEWORK_DIR = os.path.dirname(DASHBOARD_DIR)
DB_PATH_DEFAULT = os.path.join(DASHBOARD_DIR, "baza_projects.db")
EMAIL_PIPELINE_DIR = os.path.join(FRAMEWORK_DIR, "email-pipeline")
ACCOUNTS_DIR = os.path.join(EMAIL_PIPELINE_DIR, "accounts")
LEGACY_TOKEN_PATH = os.path.join(EMAIL_PIPELINE_DIR, "token.json")
CREDENTIALS_PATH = os.path.join(EMAIL_PIPELINE_DIR, "credentials.json")
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]
OLLAMA_BASE = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
DEFAULT_MODEL = os.environ.get("EMAIL_AI_MODEL", "gpt-oss:20b")
FAST_MODEL = os.environ.get("EMAIL_AI_FAST_MODEL", "gemma3:4b")


def _db_path() -> str:
    return os.environ.get("BAZA_DASHBOARD_DB", DB_PATH_DEFAULT)


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(_db_path(), timeout=8.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 8000")
    return con


# ── Schema migration ──────────────────────────────────────────────────────

_EXTRA_COLUMNS = [
    ("is_unread", "INTEGER DEFAULT 1"),
    ("is_starred", "INTEGER DEFAULT 0"),
    ("category", "TEXT"),
    ("action_items", "TEXT"),
    ("ai_summary", "TEXT"),
    ("last_synced", "TEXT"),
    ("history_id", "TEXT"),
    ("account_id", "TEXT"),
]


def _ensure_accounts_table(con: sqlite3.Connection) -> None:
    con.execute(
        """CREATE TABLE IF NOT EXISTS email_accounts (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            label TEXT,
            token_path TEXT NOT NULL,
            is_active INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            last_used TEXT
        )"""
    )


def _bootstrap_legacy_account(con: sqlite3.Connection) -> None:
    """One-shot: register the existing email-pipeline/token.json as the active account."""
    row = con.execute("SELECT COUNT(*) FROM email_accounts").fetchone()[0]
    if row > 0:
        return
    if not os.path.exists(LEGACY_TOKEN_PATH):
        return
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        import googleapiclient.discovery
        creds = Credentials.from_authorized_user_file(LEGACY_TOKEN_PATH, SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        svc = googleapiclient.discovery.build(
            "gmail", "v1", credentials=creds, cache_discovery=False
        )
        prof = svc.users().getProfile(userId="me").execute()
        email = prof["emailAddress"]
        acc_id = str(uuid.uuid4())
        # Move legacy token into accounts/<email>/token.json, leave a copy at the legacy path
        os.makedirs(ACCOUNTS_DIR, exist_ok=True)
        acc_dir = os.path.join(ACCOUNTS_DIR, email)
        os.makedirs(acc_dir, exist_ok=True)
        token_dest = os.path.join(acc_dir, "token.json")
        if not os.path.exists(token_dest):
            with open(LEGACY_TOKEN_PATH, "r") as src, open(token_dest, "w") as dst:
                dst.write(src.read())
        con.execute(
            """INSERT INTO email_accounts (id, email, label, token_path, is_active)
               VALUES (?, ?, ?, ?, 1)""",
            (acc_id, email, "Primary", token_dest)
        )
        con.commit()
        print(f"[email] bootstrapped account {email}", flush=True)
    except Exception as e:
        print(f"[email] legacy account bootstrap failed: {e}", flush=True)


def _ensure_email_schema(db_path: Optional[str] = None) -> None:
    """Add columns / indexes / FTS5 over the existing `emails` table. Idempotent."""
    path = db_path or _db_path()
    con = None
    try:
        con = sqlite3.connect(path, timeout=8.0)
        con.execute("PRAGMA busy_timeout = 8000")
        existing = {r[1] for r in con.execute("PRAGMA table_info(emails)").fetchall()}
        if not existing:
            con.execute(
                """CREATE TABLE IF NOT EXISTS emails (
                    id TEXT PRIMARY KEY, gmail_id TEXT UNIQUE, thread_id TEXT,
                    from_addr TEXT, to_addr TEXT, subject TEXT,
                    body_snippet TEXT, full_body TEXT, received_at TEXT,
                    status TEXT DEFAULT 'new', summary TEXT, suggested_reply TEXT,
                    priority TEXT DEFAULT 'normal', labels TEXT,
                    updated_at TEXT DEFAULT (datetime('now')))"""
            )
            existing = {r[1] for r in con.execute("PRAGMA table_info(emails)").fetchall()}
        for name, ddl in _EXTRA_COLUMNS:
            if name not in existing:
                try:
                    con.execute(f"ALTER TABLE emails ADD COLUMN {name} {ddl}")
                except sqlite3.OperationalError:
                    pass
        con.execute("CREATE INDEX IF NOT EXISTS idx_emails_thread ON emails(thread_id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_emails_received ON emails(received_at)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_emails_unread ON emails(is_unread)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_emails_starred ON emails(is_starred)")
        # gmail_id needs a UNIQUE index for ON CONFLICT(gmail_id) upserts.
        try:
            con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_emails_gmail_id ON emails(gmail_id)")
        except sqlite3.OperationalError:
            pass  # duplicates would prevent it; we'd need to dedupe first

        # FTS5 mirror (content-less for portability — we maintain via triggers)
        con.execute(
            """CREATE VIRTUAL TABLE IF NOT EXISTS emails_fts USING fts5(
                gmail_id UNINDEXED, subject, from_addr, body, tokenize='porter unicode61')"""
        )
        # Sync trigger writes — kept simple: full rebuild only when fts is empty.
        cnt = con.execute("SELECT COUNT(*) FROM emails_fts").fetchone()[0]
        if cnt == 0:
            con.execute(
                """INSERT INTO emails_fts(gmail_id, subject, from_addr, body)
                   SELECT gmail_id, COALESCE(subject,''), COALESCE(from_addr,''),
                          COALESCE(full_body, body_snippet, '') FROM emails"""
            )

        # Maintain on insert/update via triggers
        con.execute(
            """CREATE TRIGGER IF NOT EXISTS emails_ai_insert AFTER INSERT ON emails BEGIN
                  INSERT INTO emails_fts(gmail_id, subject, from_addr, body)
                  VALUES (new.gmail_id, COALESCE(new.subject,''),
                          COALESCE(new.from_addr,''),
                          COALESCE(new.full_body, new.body_snippet, ''));
                END"""
        )
        con.execute(
            """CREATE TRIGGER IF NOT EXISTS emails_ai_update AFTER UPDATE OF subject, from_addr, full_body, body_snippet
               ON emails BEGIN
                  DELETE FROM emails_fts WHERE gmail_id = old.gmail_id;
                  INSERT INTO emails_fts(gmail_id, subject, from_addr, body)
                  VALUES (new.gmail_id, COALESCE(new.subject,''),
                          COALESCE(new.from_addr,''),
                          COALESCE(new.full_body, new.body_snippet, ''));
                END"""
        )
        con.execute(
            """CREATE TRIGGER IF NOT EXISTS emails_ai_delete AFTER DELETE ON emails BEGIN
                  DELETE FROM emails_fts WHERE gmail_id = old.gmail_id;
                END"""
        )
        _ensure_accounts_table(con)
        _bootstrap_legacy_account(con)
        con.commit()
    except sqlite3.OperationalError as e:
        print(f"[startup] _ensure_email_schema deferred — DB busy: {e}", flush=True)
    finally:
        if con is not None:
            con.close()


# ── Gmail client (multi-account) ──────────────────────────────────────────

_gmail_cache: dict[str, tuple[Any, float]] = {}  # account_id -> (service, loaded_at)


def _active_account() -> Optional[sqlite3.Row]:
    con = _conn()
    try:
        row = con.execute(
            "SELECT * FROM email_accounts WHERE is_active=1 LIMIT 1"
        ).fetchone()
        if not row:
            row = con.execute("SELECT * FROM email_accounts LIMIT 1").fetchone()
        return row
    finally:
        con.close()


def _account_by_id(account_id: str) -> Optional[sqlite3.Row]:
    con = _conn()
    try:
        return con.execute(
            "SELECT * FROM email_accounts WHERE id=?", (account_id,)
        ).fetchone()
    finally:
        con.close()


def _pick_account(account_id: Optional[str] = None) -> Optional[sqlite3.Row]:
    if account_id:
        row = _account_by_id(account_id)
        if row:
            return row
    return _active_account()


def _gmail(account_id: Optional[str] = None):
    """Authenticated Gmail service for the given account (or active account)."""
    acc = _pick_account(account_id)
    if not acc:
        raise FileNotFoundError(
            "No Gmail account configured. Run email-pipeline/gmail_auth.py "
            "or add an account from /email."
        )
    aid = acc["id"]
    cached = _gmail_cache.get(aid)
    if cached and (time.time() - cached[1]) < 1500:
        return cached[0]
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    import googleapiclient.discovery

    token_path = acc["token_path"]
    creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(token_path, "w") as f:
            f.write(creds.to_json())
    svc = googleapiclient.discovery.build(
        "gmail", "v1", credentials=creds, cache_discovery=False
    )
    _gmail_cache[aid] = (svc, time.time())
    # Touch last_used
    try:
        con = _conn()
        con.execute(
            "UPDATE email_accounts SET last_used=datetime('now') WHERE id=?",
            (aid,)
        )
        con.commit()
        con.close()
    except Exception:
        pass
    return svc


def _req_account_id() -> Optional[str]:
    """Pull account id from current request (query string or JSON body)."""
    aid = request.args.get("account")
    if not aid and request.method != "GET":
        data = request.get_json(silent=True) or {}
        aid = data.get("account")
    return aid or None


def _decode_body(payload: dict) -> tuple[str, str]:
    """Return (plain_text, html). Walks multipart payloads."""
    plain, html = "", ""

    def walk(p):
        nonlocal plain, html
        mt = p.get("mimeType", "")
        data = (p.get("body") or {}).get("data")
        if data and mt == "text/plain" and not plain:
            plain = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        elif data and mt == "text/html" and not html:
            html = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        for sp in p.get("parts", []) or []:
            walk(sp)

    walk(payload or {})
    if not plain and html:
        plain = re.sub(r"<[^>]+>", " ", html)
        plain = re.sub(r"\s+", " ", plain).strip()
    return plain, html


def _headers_map(msg: dict) -> dict:
    return {h["name"]: h["value"] for h in (msg.get("payload") or {}).get("headers", [])}


# ── Ollama helper ─────────────────────────────────────────────────────────


def _ollama_chat(model: str, system: str, user: str,
                 temperature: float = 0.6, timeout: int = 90,
                 json_mode: bool = False) -> str:
    payload: dict[str, Any] = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "options": {"temperature": temperature},
    }
    if json_mode:
        payload["format"] = "json"
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{OLLAMA_BASE}/api/chat", data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        return (data.get("message") or {}).get("content", "")
    except Exception as e:
        print(f"[email] ollama call failed ({model}): {e}", flush=True)
        return ""


def _pick_model(prefer: Optional[str] = None) -> str:
    """Resolve an available local model, falling back through preferences."""
    if prefer:
        return prefer
    return DEFAULT_MODEL


# ── Blueprint ─────────────────────────────────────────────────────────────

email_bp = Blueprint("email_studio", __name__)


SYSTEM_LABELS = [
    ("INBOX", "Inbox", "📥"),
    ("STARRED", "Starred", "⭐"),
    ("SENT", "Sent", "📤"),
    ("DRAFT", "Drafts", "📝"),
    ("IMPORTANT", "Important", "❗"),
    ("UNREAD", "Unread", "✉️"),
    ("CATEGORY_PERSONAL", "Personal", "👤"),
    ("CATEGORY_SOCIAL", "Social", "💬"),
    ("CATEGORY_PROMOTIONS", "Promotions", "🏷️"),
    ("CATEGORY_UPDATES", "Updates", "🔔"),
    ("CATEGORY_FORUMS", "Forums", "👥"),
    ("SPAM", "Spam", "🚫"),
    ("TRASH", "Trash", "🗑️"),
]


@email_bp.route("/api/email2/labels", methods=["GET"])
def api_labels():
    try:
        svc = _gmail(_req_account_id())
        resp = svc.users().labels().list(userId="me").execute()
        labels = resp.get("labels", [])
        # Counts per label (best-effort, cheap call per label is too slow; use unread counts)
        out_sys, out_user = [], []
        sys_set = {sid for sid, _, _ in SYSTEM_LABELS}
        meta = {l["id"]: l for l in labels}
        for sid, name, emoji in SYSTEM_LABELS:
            l = meta.get(sid)
            if not l:
                continue
            out_sys.append({
                "id": sid, "name": name, "emoji": emoji,
                "unread": l.get("messagesUnread", 0),
                "total":  l.get("messagesTotal", 0),
            })
        for l in labels:
            if l["id"] in sys_set or l.get("labelListVisibility") == "labelHide":
                continue
            out_user.append({
                "id": l["id"], "name": l["name"], "emoji": "🏷️",
                "unread": l.get("messagesUnread", 0),
                "total":  l.get("messagesTotal", 0),
            })
        out_user.sort(key=lambda x: x["name"].lower())
        return jsonify({"system": out_sys, "user": out_user})
    except FileNotFoundError:
        return jsonify({"error": "Gmail token not found. Run gmail_auth.py.",
                        "system": [], "user": []}), 503
    except Exception as e:
        return jsonify({"error": str(e), "system": [], "user": []}), 500


@email_bp.route("/api/email2/threads", methods=["GET"])
def api_threads():
    """List threads in a label / query, hydrating from local cache and Gmail."""
    label = request.args.get("label", "INBOX")
    q = (request.args.get("q") or "").strip()
    limit = min(int(request.args.get("limit", "40")), 100)
    page_token = request.args.get("page_token") or None

    try:
        svc = _gmail(_req_account_id())
        kwargs = {"userId": "me", "maxResults": limit}
        if label and label != "ALL":
            kwargs["labelIds"] = [label]
        if q:
            kwargs["q"] = q
        if page_token:
            kwargs["pageToken"] = page_token
        resp = svc.users().threads().list(**kwargs).execute()
        threads = resp.get("threads", []) or []
        next_token = resp.get("nextPageToken")

        # Hydrate each thread head from local cache; if missing, fetch metadata.
        con = _conn()
        try:
            out = []
            for t in threads:
                tid = t["id"]
                row = con.execute(
                    """SELECT thread_id, subject, from_addr, to_addr, body_snippet,
                              received_at, labels, is_unread, is_starred, ai_summary,
                              category, gmail_id
                       FROM emails WHERE thread_id=? ORDER BY received_at DESC LIMIT 1""",
                    (tid,)
                ).fetchone()
                if row:
                    d = dict(row)
                    out.append({
                        "thread_id": tid,
                        "subject": d["subject"] or "(no subject)",
                        "from": d["from_addr"] or "",
                        "snippet": d["body_snippet"] or t.get("snippet", ""),
                        "received_at": d["received_at"] or "",
                        "labels": (d["labels"] or "").split(",") if d["labels"] else [],
                        "is_unread": bool(d["is_unread"]),
                        "is_starred": bool(d["is_starred"]),
                        "ai_summary": d["ai_summary"] or "",
                        "category": d["category"] or "",
                        "cached": True,
                    })
                else:
                    # Lightweight remote metadata fetch
                    msg = svc.users().threads().get(
                        userId="me", id=tid, format="metadata",
                        metadataHeaders=["From", "Subject", "Date", "To"]
                    ).execute()
                    msgs = msg.get("messages", []) or []
                    head = msgs[-1] if msgs else {}
                    hdrs = _headers_map(head)
                    labels = head.get("labelIds", []) or []
                    out.append({
                        "thread_id": tid,
                        "subject": hdrs.get("Subject", "(no subject)"),
                        "from": hdrs.get("From", ""),
                        "snippet": t.get("snippet", ""),
                        "received_at": hdrs.get("Date", ""),
                        "labels": labels,
                        "is_unread": "UNREAD" in labels,
                        "is_starred": "STARRED" in labels,
                        "ai_summary": "",
                        "category": "",
                        "cached": False,
                    })
            return jsonify({"threads": out, "next_page_token": next_token})
        finally:
            con.close()
    except FileNotFoundError:
        return jsonify({"error": "Gmail token not found.", "threads": []}), 503
    except Exception as e:
        return jsonify({"error": str(e), "threads": []}), 500


@email_bp.route("/api/email2/thread/<tid>", methods=["GET"])
def api_thread(tid: str):
    try:
        svc = _gmail(_req_account_id())
        t = svc.users().threads().get(userId="me", id=tid, format="full").execute()
        msgs = []
        con = _conn()
        try:
            for m in t.get("messages", []) or []:
                hdrs = _headers_map(m)
                plain, html = _decode_body(m.get("payload") or {})
                labels = m.get("labelIds", []) or []
                msgs.append({
                    "gmail_id": m["id"],
                    "thread_id": m.get("threadId", tid),
                    "from": hdrs.get("From", ""),
                    "to": hdrs.get("To", ""),
                    "cc": hdrs.get("Cc", ""),
                    "subject": hdrs.get("Subject", "(no subject)"),
                    "date": hdrs.get("Date", ""),
                    "body": plain,
                    "html": html,
                    "labels": labels,
                    "is_unread": "UNREAD" in labels,
                    "is_starred": "STARRED" in labels,
                    "message_id_header": hdrs.get("Message-ID") or hdrs.get("Message-Id") or "",
                })
                # Refresh cache for this message
                con.execute(
                    """INSERT INTO emails (id, gmail_id, thread_id, from_addr, to_addr,
                                            subject, body_snippet, full_body, received_at,
                                            status, priority, labels, is_unread, is_starred)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', 'normal', ?, ?, ?)
                       ON CONFLICT(gmail_id) DO UPDATE SET
                         thread_id=excluded.thread_id,
                         from_addr=excluded.from_addr, to_addr=excluded.to_addr,
                         subject=excluded.subject, body_snippet=excluded.body_snippet,
                         full_body=excluded.full_body, received_at=excluded.received_at,
                         labels=excluded.labels, is_unread=excluded.is_unread,
                         is_starred=excluded.is_starred, updated_at=datetime('now')""",
                    (str(uuid.uuid4()), m["id"], m.get("threadId", tid),
                     hdrs.get("From", ""), hdrs.get("To", ""), hdrs.get("Subject", ""),
                     m.get("snippet", ""), plain, hdrs.get("Date", ""),
                     ",".join(labels), 1 if "UNREAD" in labels else 0,
                     1 if "STARRED" in labels else 0)
                )
            con.commit()
        finally:
            con.close()
        return jsonify({"thread_id": tid, "messages": msgs})
    except FileNotFoundError:
        return jsonify({"error": "Gmail token not found.", "messages": []}), 503
    except Exception as e:
        return jsonify({"error": str(e), "messages": []}), 500


@email_bp.route("/api/email2/sync", methods=["POST"])
def api_sync():
    """Pull recent unread + last N threads from Gmail into local cache."""
    body = request.get_json(silent=True) or {}
    max_threads = int(body.get("max", 80))
    label = body.get("label", "INBOX")
    try:
        svc = _gmail(_req_account_id())
        resp = svc.users().threads().list(
            userId="me", labelIds=[label], maxResults=max_threads
        ).execute()
        threads = resp.get("threads", []) or []
        new_count = 0
        con = _conn()
        try:
            for t in threads:
                tid = t["id"]
                # Fetch the head message in metadata mode for cheap caching
                tmeta = svc.users().threads().get(
                    userId="me", id=tid, format="metadata",
                    metadataHeaders=["From", "Subject", "Date", "To"]
                ).execute()
                msgs = tmeta.get("messages", []) or []
                if not msgs:
                    continue
                head = msgs[-1]
                hdrs = _headers_map(head)
                labels = head.get("labelIds", []) or []
                existing = con.execute(
                    "SELECT id FROM emails WHERE gmail_id=?", (head["id"],)
                ).fetchone()
                if existing:
                    con.execute(
                        """UPDATE emails SET labels=?, is_unread=?, is_starred=?,
                            updated_at=datetime('now') WHERE gmail_id=?""",
                        (",".join(labels), 1 if "UNREAD" in labels else 0,
                         1 if "STARRED" in labels else 0, head["id"])
                    )
                else:
                    con.execute(
                        """INSERT INTO emails (id, gmail_id, thread_id, from_addr, to_addr,
                            subject, body_snippet, full_body, received_at, status, priority,
                            labels, is_unread, is_starred)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', 'normal', ?, ?, ?)""",
                        (str(uuid.uuid4()), head["id"], tid, hdrs.get("From", ""),
                         hdrs.get("To", ""), hdrs.get("Subject", ""),
                         t.get("snippet", ""), "", hdrs.get("Date", ""),
                         ",".join(labels), 1 if "UNREAD" in labels else 0,
                         1 if "STARRED" in labels else 0)
                    )
                    new_count += 1
            con.commit()
        finally:
            con.close()
        return jsonify({"ok": True, "scanned": len(threads), "new": new_count})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def _mime_message(to: str, subject: str, body: str,
                  cc: str = "", bcc: str = "",
                  in_reply_to: str = "", references: str = "",
                  from_addr: str = "") -> str:
    msg = MIMEMultipart("alternative")
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc
    if from_addr:
        msg["From"] = from_addr
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    msg.attach(MIMEText(body, "plain", _charset="utf-8"))
    html_body = "<pre style='font-family:inherit;white-space:pre-wrap'>" + \
                body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") + \
                "</pre>"
    msg.attach(MIMEText(html_body, "html", _charset="utf-8"))
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")


@email_bp.route("/api/email2/send", methods=["POST"])
def api_send():
    """Send compose/reply/reply_all/forward. Body: {mode, to, cc, bcc, subject, body, thread_id?, reply_to_gmail_id?}"""
    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "compose")
    to = (data.get("to") or "").strip()
    cc = (data.get("cc") or "").strip()
    bcc = (data.get("bcc") or "").strip()
    subject = (data.get("subject") or "").strip()
    body = data.get("body") or ""
    thread_id = data.get("thread_id") or None
    reply_to_gid = data.get("reply_to_gmail_id") or None

    if not to:
        return jsonify({"ok": False, "error": "Missing 'to' address"}), 400
    if not subject and mode == "compose":
        subject = "(no subject)"

    in_reply_to = ""
    references = ""
    if reply_to_gid:
        try:
            svc = _gmail(_req_account_id())
            src = svc.users().messages().get(
                userId="me", id=reply_to_gid, format="metadata",
                metadataHeaders=["Message-ID", "References", "Subject", "From"]
            ).execute()
            hdrs = _headers_map(src)
            in_reply_to = hdrs.get("Message-ID") or hdrs.get("Message-Id") or ""
            references = (hdrs.get("References", "") + " " + in_reply_to).strip()
            if not subject:
                src_subj = hdrs.get("Subject", "")
                subject = src_subj if src_subj.lower().startswith("re:") else f"Re: {src_subj}"
            if not thread_id:
                thread_id = src.get("threadId")
        except Exception as e:
            print(f"[email] reply-thread lookup failed: {e}", flush=True)

    try:
        svc = _gmail(_req_account_id())
        raw = _mime_message(to, subject, body, cc=cc, bcc=bcc,
                            in_reply_to=in_reply_to, references=references)
        send_body = {"raw": raw}
        if thread_id:
            send_body["threadId"] = thread_id
        result = svc.users().messages().send(userId="me", body=send_body).execute()
        # Best-effort: mark thread read locally
        if thread_id:
            try:
                con = _conn()
                con.execute(
                    "UPDATE emails SET status='replied', updated_at=datetime('now') WHERE thread_id=?",
                    (thread_id,)
                )
                con.commit()
                con.close()
            except Exception:
                pass
        return jsonify({"ok": True, "gmail_id": result.get("id"),
                        "thread_id": result.get("threadId")})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@email_bp.route("/api/email2/modify", methods=["POST"])
def api_modify():
    """Apply / remove labels on a thread or message. Body: {target:'thread'|'message',
       id:'<id>', add:[...], remove:[...], action?:'archive'|'trash'|'untrash'|'star'|'unstar'|'read'|'unread'}"""
    data = request.get_json(silent=True) or {}
    target = data.get("target", "thread")
    obj_id = data.get("id")
    add = list(data.get("add") or [])
    remove = list(data.get("remove") or [])
    action = data.get("action")

    if action == "archive":
        remove.append("INBOX")
    elif action == "trash":
        # Use trash endpoint directly
        pass
    elif action == "untrash":
        pass
    elif action == "star":
        add.append("STARRED")
    elif action == "unstar":
        remove.append("STARRED")
    elif action == "read":
        remove.append("UNREAD")
    elif action == "unread":
        add.append("UNREAD")

    if not obj_id:
        return jsonify({"ok": False, "error": "Missing id"}), 400

    try:
        svc = _gmail(_req_account_id())
        api = svc.users().threads() if target == "thread" else svc.users().messages()
        if action == "trash":
            api.trash(userId="me", id=obj_id).execute()
        elif action == "untrash":
            api.untrash(userId="me", id=obj_id).execute()
        else:
            api.modify(userId="me", id=obj_id,
                       body={"addLabelIds": add, "removeLabelIds": remove}).execute()

        # Sync local cache
        try:
            con = _conn()
            if target == "thread":
                if action == "read":
                    con.execute("UPDATE emails SET is_unread=0 WHERE thread_id=?", (obj_id,))
                elif action == "unread":
                    con.execute("UPDATE emails SET is_unread=1 WHERE thread_id=?", (obj_id,))
                if action == "star":
                    con.execute("UPDATE emails SET is_starred=1 WHERE thread_id=?", (obj_id,))
                elif action == "unstar":
                    con.execute("UPDATE emails SET is_starred=0 WHERE thread_id=?", (obj_id,))
                con.commit()
            con.close()
        except Exception:
            pass

        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── AI endpoints ─────────────────────────────────────────────────────────


def _truncate_body(text: str, max_chars: int = 6000) -> str:
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    head = text[: max_chars // 2]
    tail = text[-max_chars // 2 :]
    return f"{head}\n\n[...{len(text) - max_chars} chars trimmed...]\n\n{tail}"


def _thread_context(thread_id: str, limit_messages: int = 8) -> str:
    """Build a compact transcript string for a thread, from local cache if possible."""
    con = _conn()
    try:
        rows = con.execute(
            """SELECT from_addr, to_addr, subject, full_body, body_snippet, received_at
               FROM emails WHERE thread_id=? ORDER BY received_at ASC LIMIT ?""",
            (thread_id, limit_messages)
        ).fetchall()
    finally:
        con.close()
    if not rows:
        # Fetch from Gmail directly
        try:
            svc = _gmail(_req_account_id())
            t = svc.users().threads().get(userId="me", id=thread_id, format="full").execute()
            parts = []
            for m in (t.get("messages") or [])[:limit_messages]:
                hdrs = _headers_map(m)
                plain, _ = _decode_body(m.get("payload") or {})
                parts.append(
                    f"--- {hdrs.get('Date','')}\nFrom: {hdrs.get('From','')}\n"
                    f"Subject: {hdrs.get('Subject','')}\n\n{_truncate_body(plain, 3000)}"
                )
            return "\n\n".join(parts)
        except Exception as e:
            return f"(could not load thread: {e})"
    parts = []
    for r in rows:
        parts.append(
            f"--- {r['received_at']}\nFrom: {r['from_addr']}\nTo: {r['to_addr']}\n"
            f"Subject: {r['subject']}\n\n"
            f"{_truncate_body(r['full_body'] or r['body_snippet'] or '', 3000)}"
        )
    return "\n\n".join(parts)


@email_bp.route("/api/email2/ai/summarize", methods=["POST"])
def api_ai_summarize():
    data = request.get_json(silent=True) or {}
    thread_id = data.get("thread_id")
    text = data.get("text") or ""
    model = _pick_model(data.get("model"))
    if thread_id and not text:
        text = _thread_context(thread_id)
    if not text:
        return jsonify({"ok": False, "error": "no thread or text"}), 400

    out = _ollama_chat(
        model=model,
        system=("You summarize email threads for a busy executive. "
                "Be terse and factual. Output exactly: a one-line headline, then 2-4 bullets "
                "of key points, then an Action label (Reply / Read / Ignore / Wait)."),
        user=f"Summarize this thread:\n\n{_truncate_body(text, 7000)}",
        temperature=0.3,
    )
    # Persist cache if thread_id
    if thread_id and out:
        try:
            con = _conn()
            con.execute("UPDATE emails SET ai_summary=? WHERE thread_id=?", (out, thread_id))
            con.commit()
            con.close()
        except Exception:
            pass
    return jsonify({"ok": True, "summary": out, "model": model})


@email_bp.route("/api/email2/ai/draft", methods=["POST"])
def api_ai_draft():
    data = request.get_json(silent=True) or {}
    thread_id = data.get("thread_id")
    tone = data.get("tone", "professional")
    intent = (data.get("intent") or "").strip()
    length = data.get("length", "medium")
    model = _pick_model(data.get("model"))

    tone_map = {
        "professional": "professional, warm, direct",
        "friendly": "friendly, conversational, warm",
        "concise": "extremely concise — 2-3 sentences max",
        "formal": "formal and respectful",
        "decline": "polite decline; firm but courteous",
        "follow_up": "polite follow-up nudging for a response",
    }
    length_map = {
        "short": "2-3 sentences", "medium": "4-6 sentences",
        "long": "8-12 sentences", "auto": "appropriate length"
    }

    ctx = _thread_context(thread_id) if thread_id else ""
    extra = f"\n\nUser intent: {intent}" if intent else ""
    out = _ollama_chat(
        model=model,
        system=(f"You draft email replies on behalf of Serge (AHB Company / Baza Empire). "
                f"Tone: {tone_map.get(tone, tone)}. Length: {length_map.get(length, 'medium')}. "
                f"Output the reply body ONLY — no salutation header line if the thread already has one, "
                f"no signature, no 'Subject:' line, no preamble. Plain text. "
                f"If declining or saying no, be direct. Never invent facts."),
        user=f"Draft a reply to the latest message in this thread.{extra}\n\n"
             f"Thread context:\n{_truncate_body(ctx, 6000)}",
        temperature=0.5,
    )
    return jsonify({"ok": True, "draft": out, "model": model, "tone": tone})


@email_bp.route("/api/email2/ai/polish", methods=["POST"])
def api_ai_polish():
    data = request.get_json(silent=True) or {}
    text = data.get("text", "")
    tone = data.get("tone", "professional")
    instruction = data.get("instruction", "").strip()
    model = _pick_model(data.get("model"))
    if not text.strip():
        return jsonify({"ok": False, "error": "no text"}), 400

    sys_msg = (f"Rewrite the user's email draft. Goal tone: {tone}. "
               f"{'Specific instruction: ' + instruction + '. ' if instruction else ''}"
               f"Preserve meaning and any concrete facts/numbers/dates. "
               f"Output the rewritten body ONLY — no explanations, no preamble.")
    out = _ollama_chat(model=model, system=sys_msg, user=text, temperature=0.4)
    return jsonify({"ok": True, "text": out, "model": model})


@email_bp.route("/api/email2/ai/extract", methods=["POST"])
def api_ai_extract():
    """Extract structured action items / dates / amounts from a thread."""
    data = request.get_json(silent=True) or {}
    thread_id = data.get("thread_id")
    text = data.get("text") or ""
    model = _pick_model(data.get("model"))
    if thread_id and not text:
        text = _thread_context(thread_id)
    if not text:
        return jsonify({"ok": False, "error": "no thread or text"}), 400

    out = _ollama_chat(
        model=model, json_mode=True, temperature=0.2,
        system=("Extract structured info from email text. Return JSON with keys: "
                "action_items (array of strings), questions (array), dates (array of "
                "{label, when}), amounts (array of {label, value}), people (array of "
                "{name, email, role}), urgency (low|medium|high). Empty arrays if none."),
        user=f"Extract from:\n\n{_truncate_body(text, 7000)}",
    )
    try:
        parsed = json.loads(out) if out else {}
    except Exception:
        parsed = {"raw": out}
    if thread_id and parsed:
        try:
            con = _conn()
            con.execute("UPDATE emails SET action_items=? WHERE thread_id=?",
                        (json.dumps(parsed), thread_id))
            con.commit()
            con.close()
        except Exception:
            pass
    return jsonify({"ok": True, "extracted": parsed, "model": model})


@email_bp.route("/api/email2/ai/categorize", methods=["POST"])
def api_ai_categorize():
    data = request.get_json(silent=True) or {}
    thread_id = data.get("thread_id")
    text = data.get("text") or ""
    model = _pick_model(data.get("model") or FAST_MODEL)
    if thread_id and not text:
        text = _thread_context(thread_id, limit_messages=2)
    if not text:
        return jsonify({"ok": False, "error": "no thread or text"}), 400

    out = _ollama_chat(
        model=model, json_mode=True, temperature=0.1,
        system=("Classify this email. Return JSON {category, priority, needs_reply}. "
                "category ∈ {lead, customer, vendor, billing, personal, newsletter, "
                "notification, spam, internal, other}. priority ∈ {low, medium, high}. "
                "needs_reply ∈ {true, false}."),
        user=_truncate_body(text, 3500),
    )
    try:
        parsed = json.loads(out) if out else {}
    except Exception:
        parsed = {}
    if thread_id and parsed.get("category"):
        try:
            con = _conn()
            con.execute(
                "UPDATE emails SET category=?, priority=? WHERE thread_id=?",
                (parsed.get("category"), parsed.get("priority", "normal"), thread_id)
            )
            con.commit()
            con.close()
        except Exception:
            pass
    return jsonify({"ok": True, **parsed, "model": model})


@email_bp.route("/api/email2/search", methods=["GET"])
def api_search():
    q = (request.args.get("q") or "").strip()
    limit = min(int(request.args.get("limit", "40")), 100)
    if not q:
        return jsonify({"results": []})
    con = _conn()
    try:
        # FTS5 query — escape user input to avoid syntax errors
        safe = q.replace('"', '""')
        rows = con.execute(
            """SELECT e.thread_id, e.gmail_id, e.subject, e.from_addr, e.body_snippet,
                      e.received_at, e.labels, e.is_unread, e.is_starred,
                      bm25(emails_fts) AS rank
               FROM emails_fts JOIN emails e ON e.gmail_id = emails_fts.gmail_id
               WHERE emails_fts MATCH ? ORDER BY rank LIMIT ?""",
            (f'"{safe}"', limit)
        ).fetchall()
        out = []
        seen_threads = set()
        for r in rows:
            d = dict(r)
            if d["thread_id"] in seen_threads:
                continue
            seen_threads.add(d["thread_id"])
            out.append({
                "thread_id": d["thread_id"],
                "subject": d["subject"] or "(no subject)",
                "from": d["from_addr"] or "",
                "snippet": d["body_snippet"] or "",
                "received_at": d["received_at"] or "",
                "labels": (d["labels"] or "").split(",") if d["labels"] else [],
                "is_unread": bool(d["is_unread"]),
                "is_starred": bool(d["is_starred"]),
                "rank": d["rank"],
            })
        return jsonify({"results": out, "query": q})
    finally:
        con.close()


@email_bp.route("/api/email2/contacts/suggest", methods=["GET"])
def api_contact_suggest():
    """Autocomplete contact emails from history."""
    q = (request.args.get("q") or "").strip().lower()
    if len(q) < 2:
        return jsonify({"contacts": []})
    con = _conn()
    try:
        rows = con.execute(
            """SELECT from_addr, COUNT(*) AS n FROM emails
               WHERE LOWER(from_addr) LIKE ? GROUP BY from_addr ORDER BY n DESC LIMIT 12""",
            (f"%{q}%",)
        ).fetchall()
    finally:
        con.close()
    out = []
    seen = set()
    for r in rows:
        name, addr = parseaddr(r["from_addr"] or "")
        if not addr or addr in seen:
            continue
        seen.add(addr)
        out.append({"name": name, "email": addr, "raw": r["from_addr"], "count": r["n"]})
    return jsonify({"contacts": out})


@email_bp.route("/api/email2/models", methods=["GET"])
def api_models():
    """Models available for AI features on the local pool."""
    out = []
    for url in ("http://127.0.0.1:11434", "http://127.0.0.1:11435",
                "http://127.0.0.1:11438"):
        try:
            req = urllib.request.Request(f"{url}/api/tags")
            with urllib.request.urlopen(req, timeout=2) as r:
                data = json.loads(r.read().decode())
                for m in data.get("models", []):
                    if m["name"] not in out:
                        out.append(m["name"])
        except Exception:
            continue
    return jsonify({"models": out, "default": DEFAULT_MODEL, "fast": FAST_MODEL})


# ── Account management ────────────────────────────────────────────────────

# In-memory pending OAuth flows. Keyed by a short token returned to the UI.
_oauth_flows: dict[str, dict] = {}


@email_bp.route("/api/email2/accounts", methods=["GET"])
def api_accounts():
    con = _conn()
    try:
        rows = con.execute(
            "SELECT id, email, label, is_active, created_at, last_used FROM email_accounts ORDER BY is_active DESC, email ASC"
        ).fetchall()
    finally:
        con.close()
    return jsonify({"accounts": [dict(r) for r in rows]})


@email_bp.route("/api/email2/accounts/activate", methods=["POST"])
def api_accounts_activate():
    data = request.get_json(silent=True) or {}
    aid = data.get("id")
    if not aid:
        return jsonify({"ok": False, "error": "missing id"}), 400
    con = _conn()
    try:
        row = con.execute("SELECT id FROM email_accounts WHERE id=?", (aid,)).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "account not found"}), 404
        con.execute("UPDATE email_accounts SET is_active=0")
        con.execute("UPDATE email_accounts SET is_active=1 WHERE id=?", (aid,))
        con.commit()
    finally:
        con.close()
    # Invalidate per-account caches so the next request picks the new active
    _gmail_cache.clear()
    return jsonify({"ok": True})


@email_bp.route("/api/email2/accounts/<aid>", methods=["DELETE"])
def api_accounts_delete(aid: str):
    con = _conn()
    try:
        row = con.execute(
            "SELECT id, email, token_path, is_active FROM email_accounts WHERE id=?",
            (aid,)
        ).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "account not found"}), 404
        token_path = row["token_path"]
        was_active = bool(row["is_active"])
        con.execute("DELETE FROM email_accounts WHERE id=?", (aid,))
        if was_active:
            # Promote any remaining account to active
            other = con.execute(
                "SELECT id FROM email_accounts ORDER BY created_at LIMIT 1"
            ).fetchone()
            if other:
                con.execute(
                    "UPDATE email_accounts SET is_active=1 WHERE id=?",
                    (other["id"],)
                )
        con.commit()
    finally:
        con.close()
    # Remove token file (best-effort)
    try:
        if token_path and os.path.exists(token_path):
            os.remove(token_path)
    except Exception:
        pass
    _gmail_cache.pop(aid, None)
    return jsonify({"ok": True})


@email_bp.route("/api/email2/accounts/add/start", methods=["POST"])
def api_accounts_add_start():
    """Begin an OAuth flow. Returns the consent URL the user must open."""
    if not os.path.exists(CREDENTIALS_PATH):
        return jsonify({
            "ok": False,
            "error": f"OAuth client secret not found at {CREDENTIALS_PATH}"
        }), 500
    import threading
    from google_auth_oauthlib.flow import InstalledAppFlow
    data = request.get_json(silent=True) or {}
    label = (data.get("label") or "").strip() or None
    flow_id = secrets_token()

    # Build the flow once, capture the consent URL, then hand off to background.
    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
    # Reserve a port so the redirect_uri matches what local_server will use.
    import socket
    s = socket.socket(); s.bind(("localhost", 0)); port = s.getsockname()[1]; s.close()
    flow.redirect_uri = f"http://localhost:{port}/"
    auth_url, _ = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent"
    )

    def _run():
        try:
            import googleapiclient.discovery
            creds = flow.run_local_server(
                host="localhost", port=port, open_browser=False,
                authorization_prompt_message="",
                success_message="✅ Account added. Return to the Mail UI.",
            )
            svc = googleapiclient.discovery.build(
                "gmail", "v1", credentials=creds, cache_discovery=False
            )
            prof = svc.users().getProfile(userId="me").execute()
            email = prof["emailAddress"]
            os.makedirs(ACCOUNTS_DIR, exist_ok=True)
            acc_dir = os.path.join(ACCOUNTS_DIR, email)
            os.makedirs(acc_dir, exist_ok=True)
            token_dest = os.path.join(acc_dir, "token.json")
            with open(token_dest, "w") as f:
                f.write(creds.to_json())
            con = _conn()
            try:
                existing = con.execute(
                    "SELECT id FROM email_accounts WHERE email=?", (email,)
                ).fetchone()
                if existing:
                    con.execute(
                        "UPDATE email_accounts SET token_path=?, label=COALESCE(?, label) WHERE id=?",
                        (token_dest, label, existing["id"])
                    )
                    acc_id = existing["id"]
                else:
                    acc_id = str(uuid.uuid4())
                    con.execute(
                        """INSERT INTO email_accounts (id, email, label, token_path, is_active)
                           VALUES (?, ?, ?, ?, 0)""",
                        (acc_id, email, label or "", token_dest)
                    )
                con.commit()
            finally:
                con.close()
            _oauth_flows[flow_id] = {"status": "done", "email": email, "account_id": acc_id}
        except Exception as e:
            _oauth_flows[flow_id] = {"status": "failed", "error": str(e)}

    _oauth_flows[flow_id] = {"status": "pending"}
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "flow_id": flow_id, "auth_url": auth_url, "port": port})


def secrets_token() -> str:
    """Short opaque flow id."""
    import secrets as _s
    return _s.token_urlsafe(16)


@email_bp.route("/api/email2/accounts/add/poll", methods=["GET"])
def api_accounts_add_poll():
    fid = request.args.get("flow_id", "")
    info = _oauth_flows.get(fid)
    if not info:
        return jsonify({"status": "unknown"}), 404
    return jsonify(info)


# ── Cleanup endpoints ─────────────────────────────────────────────────────


def _gmail_count_query(svc, q: str) -> int:
    """Estimate how many messages match a Gmail search query."""
    try:
        r = svc.users().messages().list(userId="me", q=q, maxResults=1).execute()
        n = r.get("resultSizeEstimate")
        if n is not None:
            return int(n)
        return len(r.get("messages", []) or [])
    except Exception:
        return 0


def _gmail_list_message_ids(svc, q: str, cap: int = 2000) -> list[str]:
    """Page through messages.list to collect ids matching q, up to `cap`."""
    ids: list[str] = []
    page_token = None
    while len(ids) < cap:
        kwargs = {"userId": "me", "q": q, "maxResults": min(500, cap - len(ids))}
        if page_token:
            kwargs["pageToken"] = page_token
        r = svc.users().messages().list(**kwargs).execute()
        for m in r.get("messages", []) or []:
            ids.append(m["id"])
        page_token = r.get("nextPageToken")
        if not page_token:
            break
    return ids


def _build_query(spec: dict) -> str:
    """Translate a cleanup spec into a Gmail search query."""
    parts: list[str] = []
    if spec.get("sender"):
        parts.append(f'from:{spec["sender"]}')
    if spec.get("query"):
        parts.append(spec["query"])
    if spec.get("label"):
        parts.append(f'label:{spec["label"]}')
    older = spec.get("older_than_days")
    if older:
        parts.append(f"older_than:{int(older)}d")
    if spec.get("unread_only"):
        parts.append("is:unread")
    if spec.get("read_only"):
        parts.append("-is:unread")
    if spec.get("has_attachment"):
        parts.append("has:attachment")
    if spec.get("in"):  # 'inbox', 'spam', 'trash', 'anywhere'
        if spec["in"] == "anywhere":
            pass
        else:
            parts.append(f'in:{spec["in"]}')
    return " ".join(parts).strip() or "in:inbox"


@email_bp.route("/api/email2/cleanup/senders", methods=["GET"])
def api_cleanup_senders():
    """Top senders ranked by local-cache count. Cheap; doesn't hit Gmail."""
    limit = min(int(request.args.get("limit", "50")), 200)
    days = request.args.get("days")
    con = _conn()
    try:
        sql = """SELECT from_addr, COUNT(*) AS n,
                    SUM(CASE WHEN is_unread=1 THEN 1 ELSE 0 END) AS unread,
                    MAX(received_at) AS last_seen
                 FROM emails WHERE from_addr IS NOT NULL AND from_addr <> ''"""
        args: list[Any] = []
        if days:
            try:
                sql += " AND datetime(received_at) >= datetime('now', ?)"
                args.append(f"-{int(days)} days")
            except Exception:
                pass
        sql += " GROUP BY from_addr ORDER BY n DESC LIMIT ?"
        args.append(limit)
        rows = con.execute(sql, args).fetchall()
    finally:
        con.close()
    out = []
    for r in rows:
        name, addr = parseaddr(r["from_addr"] or "")
        out.append({
            "raw": r["from_addr"], "name": name, "email": addr,
            "count": r["n"], "unread": r["unread"], "last_seen": r["last_seen"]
        })
    return jsonify({"senders": out})


@email_bp.route("/api/email2/cleanup/scan", methods=["POST"])
def api_cleanup_scan():
    """Dry-run: how many messages would the spec match? Returns count + sample."""
    spec = request.get_json(silent=True) or {}
    q = _build_query(spec)
    try:
        svc = _gmail(_req_account_id())
        # Total estimate
        total = _gmail_count_query(svc, q)
        # Sample first 8 message metadata
        ids = _gmail_list_message_ids(svc, q, cap=8)
        sample = []
        for mid in ids:
            try:
                m = svc.users().messages().get(
                    userId="me", id=mid, format="metadata",
                    metadataHeaders=["From", "Subject", "Date"]
                ).execute()
                hdrs = _headers_map(m)
                sample.append({
                    "gmail_id": mid,
                    "from": hdrs.get("From", ""),
                    "subject": hdrs.get("Subject", ""),
                    "date": hdrs.get("Date", ""),
                    "snippet": m.get("snippet", ""),
                })
            except Exception:
                continue
        return jsonify({"ok": True, "query": q, "estimate": total, "sample": sample})
    except FileNotFoundError as e:
        return jsonify({"ok": False, "error": str(e)}), 503
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@email_bp.route("/api/email2/cleanup/bulk", methods=["POST"])
def api_cleanup_bulk():
    """Execute a cleanup operation. Body: {spec, action:'archive'|'trash'|'mark_read'|'delete', max?}
    `delete` removes permanently; `trash` moves to Trash; `archive` removes INBOX label."""
    data = request.get_json(silent=True) or {}
    spec = data.get("spec") or {}
    action = data.get("action", "")
    cap = int(data.get("max", 1000))
    confirm = bool(data.get("confirm"))
    if action not in {"archive", "trash", "mark_read", "delete"}:
        return jsonify({"ok": False, "error": "bad action"}), 400
    if not confirm:
        return jsonify({"ok": False, "error": "missing confirm=true"}), 400
    q = _build_query(spec)
    try:
        svc = _gmail(_req_account_id())
        ids = _gmail_list_message_ids(svc, q, cap=cap)
        if not ids:
            return jsonify({"ok": True, "affected": 0, "query": q})

        # Batch operations: messages.batchModify max 1000 per call; batchDelete same.
        affected = 0
        for i in range(0, len(ids), 500):
            chunk = ids[i:i+500]
            if action == "archive":
                svc.users().messages().batchModify(
                    userId="me",
                    body={"ids": chunk, "removeLabelIds": ["INBOX"]},
                ).execute()
            elif action == "mark_read":
                svc.users().messages().batchModify(
                    userId="me",
                    body={"ids": chunk, "removeLabelIds": ["UNREAD"]},
                ).execute()
            elif action == "trash":
                # No batch trash; loop. Use threads.trash where feasible to reduce calls.
                for mid in chunk:
                    try:
                        svc.users().messages().trash(userId="me", id=mid).execute()
                    except Exception:
                        continue
            elif action == "delete":
                # batchDelete removes permanently. Requires the broader scope; if it
                # fails, fall back to per-message trash.
                try:
                    svc.users().messages().batchDelete(
                        userId="me", body={"ids": chunk}
                    ).execute()
                except Exception:
                    for mid in chunk:
                        try:
                            svc.users().messages().trash(userId="me", id=mid).execute()
                        except Exception:
                            continue
            affected += len(chunk)

        # Sync local cache: drop or mark affected rows
        try:
            con = _conn()
            placeholders = ",".join(["?"] * min(len(ids), 500))
            if action in {"trash", "delete"}:
                for i in range(0, len(ids), 500):
                    chunk = ids[i:i+500]
                    con.execute(
                        f"DELETE FROM emails WHERE gmail_id IN ({','.join(['?']*len(chunk))})",
                        chunk
                    )
            elif action == "archive":
                for i in range(0, len(ids), 500):
                    chunk = ids[i:i+500]
                    con.execute(
                        f"UPDATE emails SET labels=REPLACE(COALESCE(labels,''),'INBOX,','') "
                        f"WHERE gmail_id IN ({','.join(['?']*len(chunk))})",
                        chunk
                    )
            elif action == "mark_read":
                for i in range(0, len(ids), 500):
                    chunk = ids[i:i+500]
                    con.execute(
                        f"UPDATE emails SET is_unread=0 "
                        f"WHERE gmail_id IN ({','.join(['?']*len(chunk))})",
                        chunk
                    )
            con.commit()
            con.close()
        except Exception as e:
            print(f"[email] cleanup local-cache sync failed: {e}", flush=True)

        return jsonify({"ok": True, "affected": affected, "query": q})
    except FileNotFoundError as e:
        return jsonify({"ok": False, "error": str(e)}), 503
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@email_bp.route("/api/email2/cleanup/unsubscribe_candidates", methods=["GET"])
def api_cleanup_unsubscribe_candidates():
    """List threads with a List-Unsubscribe header, grouped by sender."""
    limit = min(int(request.args.get("limit", "30")), 100)
    try:
        svc = _gmail(_req_account_id())
        # Heuristic Gmail search to find messages likely to have List-Unsubscribe
        ids = _gmail_list_message_ids(
            svc, q="(unsubscribe OR list-unsubscribe) newer_than:120d in:inbox",
            cap=limit * 3
        )
        out = []
        seen_senders = set()
        for mid in ids:
            try:
                m = svc.users().messages().get(
                    userId="me", id=mid, format="metadata",
                    metadataHeaders=["From", "Subject", "List-Unsubscribe",
                                     "List-Unsubscribe-Post", "Date"]
                ).execute()
            except Exception:
                continue
            hdrs = _headers_map(m)
            lu = hdrs.get("List-Unsubscribe", "")
            if not lu:
                continue
            sender = hdrs.get("From", "")
            _, addr = parseaddr(sender)
            if addr in seen_senders:
                continue
            seen_senders.add(addr)
            # Parse List-Unsubscribe (<https://...>, <mailto:...>)
            urls = re.findall(r"<([^>]+)>", lu)
            http_url = next((u for u in urls if u.startswith("http")), "")
            mailto = next((u for u in urls if u.startswith("mailto:")), "")
            has_one_click = "List-Unsubscribe-Post" in hdrs and "One-Click" in hdrs.get("List-Unsubscribe-Post", "")
            out.append({
                "gmail_id": mid,
                "thread_id": m.get("threadId", ""),
                "from": sender,
                "from_email": addr,
                "subject": hdrs.get("Subject", ""),
                "date": hdrs.get("Date", ""),
                "http_url": http_url,
                "mailto": mailto,
                "one_click": has_one_click,
            })
            if len(out) >= limit:
                break
        return jsonify({"candidates": out})
    except FileNotFoundError as e:
        return jsonify({"error": str(e), "candidates": []}), 503
    except Exception as e:
        return jsonify({"error": str(e), "candidates": []}), 500


@email_bp.route("/api/email2/cleanup/unsubscribe", methods=["POST"])
def api_cleanup_unsubscribe():
    """Execute a one-click unsubscribe (RFC 8058 POST) for a given gmail message."""
    data = request.get_json(silent=True) or {}
    gid = data.get("gmail_id")
    if not gid:
        return jsonify({"ok": False, "error": "missing gmail_id"}), 400
    try:
        svc = _gmail(_req_account_id())
        m = svc.users().messages().get(
            userId="me", id=gid, format="metadata",
            metadataHeaders=["From", "List-Unsubscribe", "List-Unsubscribe-Post"]
        ).execute()
        hdrs = _headers_map(m)
        lu = hdrs.get("List-Unsubscribe", "")
        urls = re.findall(r"<([^>]+)>", lu)
        http_url = next((u for u in urls if u.startswith("http")), "")
        if not http_url:
            return jsonify({"ok": False, "error": "no http unsubscribe URL"}), 400
        # RFC 8058 one-click POST
        try:
            req = urllib.request.Request(
                http_url, data=b"List-Unsubscribe=One-Click",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                status = r.status
        except Exception as e:
            return jsonify({"ok": False, "error": f"POST failed: {e}", "url": http_url}), 502
        return jsonify({"ok": True, "status": status, "url": http_url})
    except FileNotFoundError as e:
        return jsonify({"ok": False, "error": str(e)}), 503
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@email_bp.route("/api/email2/cleanup/empty", methods=["POST"])
def api_cleanup_empty():
    """Permanently empty Trash or Spam. Requires confirm=true."""
    data = request.get_json(silent=True) or {}
    target = (data.get("target") or "").lower()
    if target not in {"trash", "spam"}:
        return jsonify({"ok": False, "error": "target must be 'trash' or 'spam'"}), 400
    if not data.get("confirm"):
        return jsonify({"ok": False, "error": "missing confirm=true"}), 400
    label_id = "TRASH" if target == "trash" else "SPAM"
    try:
        svc = _gmail(_req_account_id())
        ids = _gmail_list_message_ids(svc, q=f"in:{target}", cap=5000)
        affected = 0
        for i in range(0, len(ids), 500):
            chunk = ids[i:i+500]
            try:
                svc.users().messages().batchDelete(
                    userId="me", body={"ids": chunk}
                ).execute()
            except Exception:
                for mid in chunk:
                    try:
                        svc.users().messages().delete(userId="me", id=mid).execute()
                    except Exception:
                        continue
            affected += len(chunk)
        return jsonify({"ok": True, "affected": affected, "target": target})
    except FileNotFoundError as e:
        return jsonify({"ok": False, "error": str(e)}), 503
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@email_bp.route("/api/email2/cleanup/large", methods=["GET"])
def api_cleanup_large():
    """List top-N largest messages by size estimate (uses Gmail's 'larger:' filter)."""
    limit = min(int(request.args.get("limit", "20")), 100)
    threshold_mb = float(request.args.get("threshold_mb", "1"))
    q = f"larger:{int(threshold_mb*1024*1024)} -in:trash"
    try:
        svc = _gmail(_req_account_id())
        ids = _gmail_list_message_ids(svc, q=q, cap=limit * 2)
        out = []
        for mid in ids[:limit]:
            try:
                m = svc.users().messages().get(
                    userId="me", id=mid, format="metadata",
                    metadataHeaders=["From", "Subject", "Date"]
                ).execute()
            except Exception:
                continue
            hdrs = _headers_map(m)
            out.append({
                "gmail_id": mid,
                "thread_id": m.get("threadId", ""),
                "from": hdrs.get("From", ""),
                "subject": hdrs.get("Subject", ""),
                "date": hdrs.get("Date", ""),
                "size_bytes": int(m.get("sizeEstimate", 0)),
            })
        out.sort(key=lambda x: x["size_bytes"], reverse=True)
        return jsonify({"messages": out, "threshold_mb": threshold_mb})
    except FileNotFoundError as e:
        return jsonify({"error": str(e), "messages": []}), 503
    except Exception as e:
        return jsonify({"error": str(e), "messages": []}), 500
