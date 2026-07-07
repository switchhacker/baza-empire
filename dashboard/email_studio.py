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
import html as _htmlmod  # aliased: _sanitize_email_html uses a local var named `html`
import json
import os
import re
import sqlite3
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import uuid
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parseaddr
from typing import Any, Iterable, Optional

from flask import Blueprint, jsonify, request

DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))
FRAMEWORK_DIR = os.path.dirname(DASHBOARD_DIR)
DB_PATH_DEFAULT = os.path.join(DASHBOARD_DIR, "baza_projects.db")
ARTIFACTS_DIR = os.path.join(DASHBOARD_DIR, "artifacts")
_MAX_ATTACH_BYTES = 25 * 1024 * 1024
_DENY_ARTIFACT_DIRS = (".private-inbound", ".vault_meta")
EMAIL_PIPELINE_DIR = os.path.join(FRAMEWORK_DIR, "email-pipeline")
OUTBOX_DIR = os.environ.get("EMAIL_OUTBOX_DIR") or os.path.join(EMAIL_PIPELINE_DIR, ".outbox_uploads")
DESKTOP_SAVE_DIR = os.environ.get(
    "EMAIL_DESKTOP_SAVE_DIR", "/home/switchhacker/Desktop/Email-Attachments"
)


def _sweep_outbox(max_age=6 * 3600):
    """Delete staged upload dirs older than max_age seconds (orphans)."""
    import shutil
    try:
        now = time.time()
        for name in os.listdir(OUTBOX_DIR):
            p = os.path.join(OUTBOX_DIR, name)
            try:
                if os.path.isdir(p) and now - os.path.getmtime(p) > max_age:
                    shutil.rmtree(p, ignore_errors=True)
            except Exception:
                pass
    except FileNotFoundError:
        pass


os.makedirs(OUTBOX_DIR, exist_ok=True)
_sweep_outbox()
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
# Fixed loopback redirect for the add-account flow. Works automatically when the
# browser runs on baza (or through an SSH tunnel); remote browsers paste the
# redirect URL back instead (see /accounts/add/finish).
OAUTH_REDIRECT_URI = os.environ.get(
    "EMAIL_OAUTH_REDIRECT", "http://localhost:8888/api/email2/oauth/callback"
)
# Google sometimes returns extra scopes (openid, …); don't fail the exchange.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")


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
    ("has_attachments", "INTEGER DEFAULT 0"),
    ("attachments_json", "TEXT"),
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

# NOTE: services are NO LONGER cached. A cached googleapiclient service holds a
# single httplib2/OpenSSL connection that is not thread-safe; Flask's dev server
# runs threaded (app.run(threaded=True)), so concurrent requests ("All inboxes"
# fires threads+labels+sync at once) reusing one connection corrupted the TLS
# stream -> [SSL: WRONG_VERSION_NUMBER]. _gmail() now builds a fresh service
# (its own connection) per call. This dict is retained only so the existing
# invalidation calls (account activate/remove) stay harmless no-ops.
_gmail_cache: dict[str, tuple[Any, float]] = {}  # account_id -> (service, loaded_at) — unused
# Per-account locks guard token refresh + token.json writes so concurrent
# requests don't race on the same credential file at expiry.
_token_locks: dict[str, threading.Lock] = {}
_token_locks_guard = threading.Lock()


def _token_lock(aid: str) -> threading.Lock:
    with _token_locks_guard:
        lk = _token_locks.get(aid)
        if lk is None:
            lk = _token_locks[aid] = threading.Lock()
        return lk


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
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    import googleapiclient.discovery

    token_path = acc["token_path"]
    # Load + refresh credentials under a per-account lock so concurrent requests
    # can't race on writing token.json at the expiry boundary. The lock is held
    # only for the (local, fast) refresh — NOT for the API calls themselves.
    with _token_lock(aid):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(token_path, "w") as f:
                f.write(creds.to_json())
    # Build a FRESH service (its own httplib2 connection) on every call so no
    # connection is shared across Flask worker threads. static_discovery=True
    # uses the discovery doc bundled in googleapiclient, so this is cheap
    # (~1ms, no network) — the reason the old cache existed no longer applies.
    svc = googleapiclient.discovery.build(
        "gmail", "v1", credentials=creds,
        cache_discovery=False, static_discovery=True,
    )
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


def _all_accounts() -> list:
    """Return all configured Gmail accounts ordered by email."""
    con = _conn()
    try:
        rows = con.execute("SELECT * FROM email_accounts ORDER BY email ASC").fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


class _LazyGmail:
    """Defers building the real Gmail service until first attribute access, so a
    cache-hit hydration (which never touches the service) builds nothing."""
    __slots__ = ("_aid", "_svc")

    def __init__(self, aid):
        self._aid = aid
        self._svc = None

    def __getattr__(self, name):
        # Only reached for attributes not in __slots__ (e.g. .users()).
        if self._svc is None:
            self._svc = _gmail(self._aid)
        return getattr(self._svc, name)


def _threads_all(limit: int, q: str):
    """Fetch threads from every account, merge, sort descending by received_at.

    Fetched concurrently in two bounded phases: (1) list thread stubs per
    account, (2) hydrate every stub. Each task builds its OWN Gmail service and
    opens its OWN sqlite connection — never sharing a connection across threads
    (a shared httplib2/OpenSSL socket is not thread-safe and was the cause of
    the [SSL: WRONG_VERSION_NUMBER] error). _gmail() is cheap now
    (static_discovery, no network), so per-task construction is fine.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    accounts = _all_accounts()
    if not accounts:
        return jsonify({"threads": [], "next_page_token": None})

    def _list_account(acc):
        """List thread stubs for one account; returns [(acc, stub), ...]."""
        try:
            svc = _gmail(acc["id"])
            kwargs = {"userId": "me", "maxResults": limit, "labelIds": ["INBOX"]}
            if q:
                kwargs["q"] = q
            resp = svc.users().threads().list(**kwargs).execute()
            return [(acc, t) for t in (resp.get("threads", []) or [])]
        except Exception as e:
            print(f"[email] ALL-inbox list failed for {acc.get('email')}: {e}", flush=True)
            return []

    def _fetch_one(acc, t):
        """Hydrate one stub with its own connection (thread-safe). The Gmail
        service is built lazily — a cache hit never builds one (avoids a
        needless last_used UPDATE+commit per cached thread)."""
        con = _conn()
        try:
            return _hydrate_thread(_LazyGmail(acc["id"]), con, t, acc["id"], acc["email"])
        finally:
            con.close()

    # Phase 1 — list every account concurrently.
    with ThreadPoolExecutor(max_workers=min(8, len(accounts))) as ex:
        stubs = [pair for sub in ex.map(_list_account, accounts) for pair in sub]

    # Phase 2 — hydrate every stub concurrently (cache hit = db read, miss = gmail get).
    merged = []
    if stubs:
        with ThreadPoolExecutor(max_workers=min(12, len(stubs))) as ex:
            futures = [ex.submit(_fetch_one, acc, t) for acc, t in stubs]
            for fut in as_completed(futures):
                try:
                    merged.append(fut.result())
                except Exception as e:
                    print(f"[email] ALL-inbox hydrate failed: {e}", flush=True)

    merged.sort(key=lambda x: x.get("received_at") or "", reverse=True)
    return jsonify({"threads": merged[:limit], "next_page_token": None})


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


def _collect_attachments(payload: dict) -> list[dict]:
    """Walk a payload tree collecting attachments.

    Recurses into nested parts (including forwarded message/rfc822 subtrees)
    and also collects inline content-id parts (embedded images), flagged
    ``inline=True`` so the UI can distinguish them from real attachments.
    """
    out: list[dict] = []

    def walk(p):
        fn = p.get("filename") or ""
        body = p.get("body") or {}
        hdrs = {(h.get("name") or "").lower(): (h.get("value") or "")
                for h in p.get("headers", []) or []}
        cid = (hdrs.get("content-id") or "").strip("<> ")
        disp = (hdrs.get("content-disposition") or "").lower()
        if body.get("attachmentId") and (fn or cid):
            mime = p.get("mimeType", "")
            if not fn:
                ext = (mime.split("/")[-1] or "bin") if "/" in mime else "bin"
                fn = f"inline-{cid or 'part'}.{ext}"
            out.append({
                "filename": fn,
                "mime": mime,
                "size": int(body.get("size") or 0),
                "attachment_id": body["attachmentId"],
                "content_id": cid,
                "inline": bool(cid) and "attachment" not in disp,
            })
        for sp in p.get("parts", []) or []:
            walk(sp)

    walk(payload or {})
    return out


def _sanitize_email_html(html: str, cid_map: Optional[dict] = None) -> str:
    """Best-effort stdlib sanitizer for rendering email HTML in a sandboxed,
    script-less iframe. Removes active content; keeps formatting/styles."""
    if not html:
        return ""
    # scripts (with content) and other active/embedding elements (tags only)
    html = re.sub(r"(?is)<script\b[^>]*>.*?</script\s*>", "", html)
    html = re.sub(r"(?is)<script\b[^>]*/?>", "", html)
    html = re.sub(r"(?is)</?(?:object|embed|iframe|frame|frameset|applet|base|form|input|button|select|textarea|meta|link)\b[^>]*>", "", html)
    # inline event handlers:  onload="..." / onclick='...' / onerror=x
    # HTML5 also allows "/" as an attribute separator (e.g. <svg/onload=alert(1)>,
    # <img/onerror=alert(1) src=x>), so a bare leading \s missed those. Match
    # either whitespace or "/" immediately before the on* attribute name.
    #
    # The unquoted-value alternative is quote-context-blind: matched purely
    # locally, "[\s/]on\w+\s*=" can also fire in the middle of a *different*,
    # already-quoted attribute value or link text that merely contains
    # "on<word>=" as a substring — a URL path/query segment like
    # ".../on2=abc?online=1" or a title like "settings/onload=danger". The
    # old `[^\s>]+` value class greedily crossed the enclosing quote,
    # truncating the attribute and sometimes eating the closing tag. Simply
    # excluding quote chars from the value class isn't enough by itself: it
    # still lets the match start inside someone else's quoted value and
    # truncate it at the first quote it hits. So the unquoted alternative is
    # additionally required to end where HTML5 would really end an unquoted
    # attribute value — at whitespace or ">" (a lookahead, not consumed) —
    # not at a quote character. If ending at a quote is the only way to
    # match, that's a sign the "match" is actually inside another attribute's
    # quoted value, so the whole alternative fails there and nothing is
    # stripped, leaving the benign markup (and its closing quote/tag) intact.
    # A truly unquoted handler like <svg/onload=alert(1)> still strips, since
    # its value is properly terminated by ">" (or whitespace, e.g.
    # <img/onerror=alert(1) src=x>).
    html = re.sub(
        r"""(?is)[\s/]on\w+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>"']+(?=[\s>]|$))""",
        "",
        html,
    )

    # javascript:/vbscript: URLs in URL-bearing attributes. A literal-string
    # match on "javascript:"/"vbscript:" is bypassable because browsers decode
    # HTML entities before parsing the URL, and strip ASCII whitespace/control
    # chars (\x00-\x20) from the scheme per the WHATWG URL spec — e.g.
    # href="javascript&colon;alert(1)" or "java\tscript:alert(1)". Normalize
    # each captured attribute value the same way before checking the scheme.
    def _neutralize_url_attr(m: "re.Match[str]") -> str:
        attr = m.group(1)
        dq, sq, uq = m.group(3), m.group(4), m.group(5)
        if dq is not None:
            value, quote = dq, '"'
        elif sq is not None:
            value, quote = sq, "'"
        else:
            value, quote = uq, None
        normalized = _htmlmod.unescape(value or "")
        normalized = re.sub(r"[\x00-\x20]+", "", normalized).lower()
        if normalized.startswith("javascript:") or normalized.startswith("vbscript:"):
            return f'{attr}="#"' if quote else f"{attr}=#"
        return m.group(0)

    html = re.sub(
        r"""(?is)\b(href|src|action|formaction|background|poster)\s*=\s*("([^"]*)"|'([^']*)'|([^\s>]+))""",
        _neutralize_url_attr,
        html,
    )
    # javascript:/vbscript: in CSS url() — same class of bypass as the URL
    # attributes above (entity-encoded colon, embedded whitespace/control
    # chars splitting the scheme), so apply the same normalize-then-check
    # callback instead of a literal-substring match.
    def _neutralize_css_url(m: "re.Match[str]") -> str:
        inner = _htmlmod.unescape(m.group(1) or "")
        inner = re.sub(r"[\x00-\x20]+", "", inner).lower()
        if inner.startswith("javascript:") or inner.startswith("vbscript:"):
            return "url(#)"
        return m.group(0)

    html = re.sub(r"(?is)url\(\s*['\"]?([^)]*)\)", _neutralize_css_url, html)
    # cid: image rewrite to our inline attachment URLs
    if cid_map:
        def _cid(m):
            url = cid_map.get(m.group(2).strip("<> "))
            return (m.group(1) + url) if url else m.group(0)
        html = re.sub(r"(?is)(src\s*=\s*[\"']?)cid:([^\"'>\s]+)", _cid, html)
    return html


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


# ── Per-thread hydration helper ──────────────────────────────────────────

def _hydrate_thread(svc, con, t, account_id, account_email):
    """Hydrate a single thread dict from local cache or lightweight remote fetch.

    Stamps ``account_id`` and ``account_email`` onto every returned dict so
    callers never need to re-attach them.
    """
    tid = t["id"]
    row = con.execute(
        """SELECT thread_id, subject, from_addr, to_addr, body_snippet,
                  received_at, labels, is_unread, is_starred, ai_summary,
                  category, gmail_id, has_attachments, attachments_json
           FROM emails WHERE thread_id=? ORDER BY received_at DESC LIMIT 1""",
        (tid,)
    ).fetchone()
    if row:
        d = dict(row)
        try:
            _atts = json.loads(d["attachments_json"]) if d["attachments_json"] else []
        except Exception:
            _atts = []
        for _a in _atts:
            _a.setdefault("gmail_id", d["gmail_id"])
        out = {
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
            "has_attachments": bool(d["has_attachments"]),
            "attachments": _atts,
            "cached": True,
        }
    else:
        msg = svc.users().threads().get(
            userId="me", id=tid, format="metadata",
            metadataHeaders=["From", "Subject", "Date", "To"]
        ).execute()
        msgs = msg.get("messages", []) or []
        head = msgs[-1] if msgs else {}
        hdrs = _headers_map(head)
        labels = head.get("labelIds", []) or []
        out = {
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
            "has_attachments": False,
            "attachments": [],
            "cached": False,
        }
    out["account_id"] = account_id
    out["account_email"] = account_email
    return out


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

    if _req_account_id() == "ALL":
        return _threads_all(limit, q)

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
        acc = _pick_account(_req_account_id())
        acc_id = acc["id"] if acc else None
        acc_email = acc["email"] if acc else ""
        con = _conn()
        try:
            out = [_hydrate_thread(svc, con, t, acc_id, acc_email) for t in threads]
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
        acc = _pick_account(_req_account_id())
        acc_id = acc["id"] if acc else None
        svc = _gmail(acc_id)
        t = svc.users().threads().get(userId="me", id=tid, format="full").execute()
        msgs = []
        con = _conn()
        try:
            for m in t.get("messages", []) or []:
                hdrs = _headers_map(m)
                plain, html = _decode_body(m.get("payload") or {})
                labels = m.get("labelIds", []) or []
                atts = _collect_attachments(m.get("payload") or {})
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
                    "attachments": atts,
                })
                # Refresh cache for this message
                con.execute(
                    """INSERT INTO emails (id, gmail_id, thread_id, from_addr, to_addr,
                                            subject, body_snippet, full_body, received_at,
                                            status, priority, labels, is_unread, is_starred,
                                            account_id, has_attachments, attachments_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', 'normal', ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(gmail_id) DO UPDATE SET
                         thread_id=excluded.thread_id,
                         from_addr=excluded.from_addr, to_addr=excluded.to_addr,
                         subject=excluded.subject, body_snippet=excluded.body_snippet,
                         full_body=excluded.full_body, received_at=excluded.received_at,
                         labels=excluded.labels, is_unread=excluded.is_unread,
                         is_starred=excluded.is_starred,
                         account_id=COALESCE(excluded.account_id, account_id),
                         has_attachments=excluded.has_attachments,
                         attachments_json=excluded.attachments_json,
                         updated_at=datetime('now')""",
                    (str(uuid.uuid4()), m["id"], m.get("threadId", tid),
                     hdrs.get("From", ""), hdrs.get("To", ""), hdrs.get("Subject", ""),
                     m.get("snippet", ""), plain, hdrs.get("Date", ""),
                     ",".join(labels), 1 if "UNREAD" in labels else 0,
                     1 if "STARRED" in labels else 0, acc_id,
                     1 if atts else 0, json.dumps(atts))
                )
            con.commit()
        finally:
            con.close()
        return jsonify({"thread_id": tid, "messages": msgs})
    except FileNotFoundError:
        return jsonify({"error": "Gmail token not found.", "messages": []}), 503
    except Exception as e:
        return jsonify({"error": str(e), "messages": []}), 500


@email_bp.route("/api/email2/attachment/<msg_id>/<path:att_id>", methods=["GET"])
def api_attachment(msg_id: str, att_id: str):
    """Stream an attachment's bytes from Gmail. ?name= sets the download filename."""
    from flask import Response
    name = request.args.get("name") or "attachment"
    safe_name = re.sub(r'[^\w.\- ()]', "_", name)[:160] or "attachment"
    mime = request.args.get("mime") or "application/octet-stream"
    try:
        svc = _gmail(_req_account_id())
        att = svc.users().messages().attachments().get(
            userId="me", messageId=msg_id, id=att_id
        ).execute()
        data = base64.urlsafe_b64decode(att.get("data", ""))
        inline = request.args.get("inline") in ("1", "true", "yes")
        disp = "inline" if inline else "attachment"
        return Response(data, mimetype=mime, headers={
            "Content-Disposition": f'{disp}; filename="{safe_name}"',
            "Content-Length": str(len(data)),
        })
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@email_bp.route("/api/email2/message/<msg_id>/html", methods=["GET"])
def api_message_html(msg_id: str):
    """Full sanitized HTML body of one message, for the reader's sandboxed iframe."""
    from flask import Response
    try:
        acc = request.args.get("account") or ""
        svc = _gmail(_req_account_id())
        m = svc.users().messages().get(userId="me", id=msg_id, format="full").execute()
        payload = m.get("payload") or {}
        _plain, html = _decode_body(payload)
        if not (html or "").strip():
            return jsonify({"error": "no html part"}), 404
        cid_map = {}
        for a in _collect_attachments(payload):
            if a.get("content_id") and a.get("attachment_id"):
                cid_map[a["content_id"]] = (
                    "/api/email2/attachment/" + urllib.parse.quote(m.get("id", msg_id))
                    + "/" + urllib.parse.quote(a["attachment_id"])
                    + "?inline=1&name=" + urllib.parse.quote(a.get("filename") or "inline")
                    + "&mime=" + urllib.parse.quote(a.get("mime") or "")
                    + (("&account=" + urllib.parse.quote(acc)) if acc else ""))
        doc = ("<!doctype html><html><head><meta charset='utf-8'>"
               "<base target='_blank'>"
               "<style>body{margin:14px;font-family:system-ui,-apple-system,sans-serif;"
               "background:#fff;color:#111;word-wrap:break-word;overflow-wrap:break-word}"
               "img{max-width:100%;height:auto}table{max-width:100%}</style></head><body>"
               + _sanitize_email_html(html, cid_map) + "</body></html>")
        return Response(doc, mimetype="text/html", headers={
            "Content-Security-Policy": "script-src 'none'; object-src 'none'; frame-src 'none'",
            "X-Content-Type-Options": "nosniff",
        })
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _run_print_skill(job: dict) -> dict:
    """Invoke skills/shared/print_document.py and return its parsed JSON result."""
    skill = os.path.join(os.path.dirname(DASHBOARD_DIR), "skills", "shared", "print_document.py")
    venv_py = os.path.join(os.path.dirname(DASHBOARD_DIR), "venv", "bin", "python")
    py = venv_py if os.path.exists(venv_py) else "python3"
    env = os.environ.copy()
    env["SKILL_ARGS"] = json.dumps(job)
    out = subprocess.run([py, skill], capture_output=True, text=True, timeout=30, env=env)
    for line in reversed(out.stdout.strip().split("\n")):
        if line.strip().startswith("{"):
            try:
                return json.loads(line.strip())
            except Exception:
                pass
    return {"success": out.returncode == 0, "output": out.stdout.strip(),
            "stderr": out.stderr.strip()}


@email_bp.route("/api/email2/thread/<tid>/print", methods=["POST"])
def api_thread_print(tid: str):
    """Print an email thread (or a single message via body {gmail_id}) to the HP printer."""
    body = request.get_json(silent=True) or {}
    only_id = body.get("gmail_id")
    copies = int(body.get("copies", 1) or 1)
    try:
        svc = _gmail(_req_account_id())
        t = svc.users().threads().get(userId="me", id=tid, format="full").execute()
    except FileNotFoundError as e:
        return jsonify({"success": False, "error": str(e)}), 503
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    msgs = t.get("messages", []) or []
    if only_id:
        msgs = [m for m in msgs if m.get("id") == only_id]
    if not msgs:
        return jsonify({"success": False, "error": "no messages"}), 404
    subject = _headers_map(msgs[0]).get("Subject", "(no subject)")
    blocks = []
    for m in msgs:
        hdrs = _headers_map(m)
        plain, html = _decode_body(m.get("payload") or {})
        atts = _collect_attachments(m.get("payload") or {})
        att_line = ("\nAttachments: " + ", ".join(a["filename"] for a in atts)) if atts else ""
        blocks.append(
            f"From: {hdrs.get('From','')}\n"
            f"To: {hdrs.get('To','')}\n"
            f"Date: {hdrs.get('Date','')}\n"
            f"Subject: {hdrs.get('Subject','')}{att_line}\n"
            f"{'-'*64}\n{(plain or '').strip()}\n"
        )
    text = ("\n" + "=" * 64 + "\n").join(blocks)
    res = _run_print_skill({"action": "print", "text": text, "title": subject, "copies": copies})
    return jsonify(res), (200 if res.get("success") else 500)


# Classification labels offered when saving an incoming attachment to a project.
ATTACHMENT_FILE_TYPES = ["Permit", "Contract", "Invoice", "Quote", "Estimate",
                         "Receipt", "Photo", "Plan/Drawing", "Insurance", "Other"]


@email_bp.route("/api/email2/attachment/file-types", methods=["GET"])
def api_attachment_file_types():
    return jsonify({"types": ATTACHMENT_FILE_TYPES})


@email_bp.route("/api/email2/attachment/save", methods=["POST"])
def api_attachment_save():
    """Save an incoming Gmail attachment to a project's files and/or the cloud library.

    Body: {msg_id, att_id, name, mime, project_id?, file_type?, to_cloud?, to_desktop?}
    At least one of project_id (save to project files), to_cloud, or to_desktop must be set.
    """
    body = request.get_json(silent=True) or {}
    msg_id = body.get("msg_id")
    att_id = body.get("att_id")
    if not msg_id or not att_id:
        return jsonify({"success": False, "error": "msg_id and att_id required"}), 400
    project_id = (body.get("project_id") or "").strip()
    to_cloud = bool(body.get("to_cloud"))
    to_desktop = bool(body.get("to_desktop"))
    if not project_id and not to_cloud and not to_desktop:
        return jsonify({"success": False, "error": "pick a project, the cloud library, and/or Desktop"}), 400
    file_type = (body.get("file_type") or "Other").strip()
    name = body.get("name") or "attachment"
    safe = re.sub(r'[^\w.\- ()]', "_", os.path.basename(name))[:160] or "attachment"
    mime = body.get("mime") or "application/octet-stream"

    try:
        svc = _gmail(_req_account_id())
        att = svc.users().messages().attachments().get(
            userId="me", messageId=msg_id, id=att_id).execute()
        data = base64.urlsafe_b64decode(att.get("data", ""))
    except FileNotFoundError as e:
        return jsonify({"success": False, "error": str(e)}), 503
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    saved = {}
    ext = os.path.splitext(safe)[1].lstrip(".").lower()

    # 1) Save into the project's files (artifacts dir + ahb_files row)
    if project_id:
        try:
            base = os.path.realpath(os.path.join(ARTIFACTS_DIR, project_id))
            if not base.startswith(os.path.realpath(ARTIFACTS_DIR)):
                return jsonify({"success": False, "error": "bad project_id"}), 400
            dest_dir = os.path.join(base, "email-attachments")
            os.makedirs(dest_dir, exist_ok=True)
            dest = os.path.join(dest_dir, safe)
            with open(dest, "wb") as fh:
                fh.write(data)
            con = _conn()
            try:
                con.execute(
                    """INSERT INTO ahb_files (id, name, file_type, file_path, size, tags,
                                              category, year, project_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (uuid.uuid4().hex, safe, ext, dest, len(data),
                     "email", file_type, "", project_id))
                con.commit()
            finally:
                con.close()
            saved["project"] = {"project_id": project_id, "path": dest, "file_type": file_type}
        except Exception as e:
            return jsonify({"success": False, "error": f"project save failed: {e}"}), 500

    # 2) Save into the cloud library (/mnt/empirepool/cloud/1 + baza_cloud_files)
    if to_cloud:
        try:
            cloud_dir = os.path.join("/mnt/empirepool/cloud", "1", "Email-Attachments")
            os.makedirs(cloud_dir, exist_ok=True)
            cdest = os.path.join(cloud_dir, safe)
            if os.path.exists(cdest):
                stem, dot, e2 = safe.partition(".")
                cdest = os.path.join(cloud_dir, f"{stem}_{uuid.uuid4().hex[:6]}{dot}{e2}")
            with open(cdest, "wb") as fh:
                fh.write(data)
            con = _conn()
            try:
                con.execute(
                    """INSERT INTO baza_cloud_files (id, user_id, filename, path, size,
                                                     mime_type, category)
                       VALUES (?, '1', ?, ?, ?, ?, 'email')""",
                    (uuid.uuid4().hex, os.path.basename(cdest), cdest, len(data), mime))
                con.commit()
            finally:
                con.close()
            saved["cloud"] = {"path": cdest}
        except Exception as e:
            saved["cloud_error"] = str(e)

    # 3) Save to the Desktop folder (quick local grab)
    if to_desktop:
        try:
            os.makedirs(DESKTOP_SAVE_DIR, exist_ok=True)
            ddest = os.path.join(DESKTOP_SAVE_DIR, safe)
            if os.path.exists(ddest):
                stem, dot, e2 = safe.partition(".")
                ddest = os.path.join(DESKTOP_SAVE_DIR, f"{stem}_{uuid.uuid4().hex[:6]}{dot}{e2}")
            with open(ddest, "wb") as fh:
                fh.write(data)
            saved["desktop"] = {"path": ddest}
        except Exception as e:
            saved["desktop_error"] = str(e)

    return jsonify({"success": True, "saved": saved, "filename": safe})


@email_bp.route("/api/email2/attachments/upload", methods=["POST"])
def api_attachment_upload():
    """Stage an uploaded file for sending. Returns {ok, token, filename, size, mime}."""
    import mimetypes
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "no file"}), 400
    cl = request.content_length
    if cl and cl > _MAX_ATTACH_BYTES:
        return jsonify({"ok": False, "error": "file exceeds the 25 MB limit"}), 400
    token = uuid.uuid4().hex
    safe = re.sub(r'[^\w.\- ()]', "_", os.path.basename(f.filename))[:160] or "file"
    d = os.path.join(OUTBOX_DIR, token)
    os.makedirs(d, exist_ok=True)
    dest = os.path.join(d, safe)
    f.save(dest)
    size = os.path.getsize(dest)
    if size > _MAX_ATTACH_BYTES:
        import shutil
        shutil.rmtree(d, ignore_errors=True)
        return jsonify({"ok": False, "error": "file exceeds the 25 MB limit"}), 400
    mime = mimetypes.guess_type(safe)[0] or "application/octet-stream"
    return jsonify({"ok": True, "token": token, "filename": safe, "size": size, "mime": mime})


_DOC_EXTS = {"doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "csv", "txt", "md", "rtf"}


def _att_type_bucket(mime: str, name: str) -> str:
    mime = (mime or "").lower()
    ext = os.path.splitext(name or "")[1].lstrip(".").lower()
    if mime.startswith("image/") or ext in ("jpg", "jpeg", "png", "gif", "webp", "bmp", "heic", "svg"):
        return "image"
    if mime == "application/pdf" or ext == "pdf":
        return "pdf"
    if mime.startswith("video/") or ext in ("mp4", "mov", "m4v", "webm", "avi"):
        return "video"
    if mime.startswith("audio/") or ext in ("mp3", "wav", "m4a", "ogg", "flac"):
        return "audio"
    if ext in _DOC_EXTS or "word" in mime or "excel" in mime or "spreadsheet" in mime \
            or "presentation" in mime or mime.startswith("text/"):
        return "doc"
    return "other"


@email_bp.route("/api/email2/attachments/browse", methods=["GET"])
def api_attachments_browse():
    """Browse cached attachments across all mailboxes/accounts. Local cache only."""
    q = (request.args.get("q") or "").strip().lower()
    ftype = (request.args.get("type") or "").strip().lower()
    acc = (request.args.get("account") or "").strip()
    limit = max(1, min(int(request.args.get("limit", 100) or 100), 500))
    offset = max(0, int(request.args.get("offset", 0) or 0))
    con = _conn()
    try:
        sql = ("SELECT gmail_id, thread_id, subject, from_addr, received_at, account_id, "
               "attachments_json FROM emails WHERE has_attachments=1")
        params: list = []
        if acc and acc != "ALL":
            sql += " AND account_id=?"
            params.append(acc)
        sql += " ORDER BY received_at DESC LIMIT 1000"
        rows = con.execute(sql, params).fetchall()
    finally:
        con.close()
    out = []
    for r in rows:
        try:
            atts = json.loads(r["attachments_json"] or "[]")
        except Exception:
            atts = []
        for a in atts:
            if a.get("inline"):
                continue
            name = a.get("filename") or ""
            hay = " ".join([name, r["subject"] or "", r["from_addr"] or ""]).lower()
            if q and q not in hay:
                continue
            if ftype and _att_type_bucket(a.get("mime", ""), name) != ftype:
                continue
            out.append({
                "gmail_id": r["gmail_id"], "thread_id": r["thread_id"],
                "subject": r["subject"] or "", "from_addr": r["from_addr"] or "",
                "received_at": r["received_at"] or "", "account_id": r["account_id"] or "",
                "filename": name, "mime": a.get("mime", ""),
                "size": a.get("size") or 0, "attachment_id": a.get("attachment_id", ""),
            })
    return jsonify({"attachments": out[offset:offset + limit], "total": len(out)})


@email_bp.route("/api/email2/attachments/index", methods=["POST"])
def api_attachments_index():
    """Backfill attachments_json for recent threads (full-format fetch).
    Body: {max?: int, label?: str}. Returns {ok, indexed}."""
    body = request.get_json(silent=True) or {}
    max_threads = max(1, min(int(body.get("max", 50) or 50), 200))
    label = body.get("label") or "INBOX"
    try:
        acc = _pick_account(_req_account_id())
        acc_id = acc["id"] if acc else None
        svc = _gmail(acc_id)
        resp = svc.users().threads().list(
            userId="me", labelIds=[label], maxResults=max_threads).execute()
        indexed = 0
        con = _conn()
        try:
            for t in resp.get("threads", []) or []:
                full = svc.users().threads().get(userId="me", id=t["id"], format="full").execute()
                for m in full.get("messages", []) or []:
                    atts = _collect_attachments(m.get("payload") or {})
                    cur = con.execute("SELECT 1 FROM emails WHERE gmail_id=?", (m["id"],)).fetchone()
                    if cur:
                        con.execute(
                            "UPDATE emails SET has_attachments=?, attachments_json=? WHERE gmail_id=?",
                            (1 if atts else 0, json.dumps(atts), m["id"]))
                    else:
                        hdrs = _headers_map(m)
                        con.execute(
                            """INSERT INTO emails (id, gmail_id, thread_id, from_addr, subject,
                                   body_snippet, received_at, status, priority, account_id,
                                   has_attachments, attachments_json)
                               VALUES (?, ?, ?, ?, ?, ?, ?, 'new', 'normal', ?, ?, ?)""",
                            (str(uuid.uuid4()), m["id"], m.get("threadId", t["id"]),
                             hdrs.get("From", ""), hdrs.get("Subject", ""), m.get("snippet", ""),
                             hdrs.get("Date", ""), acc_id, 1 if atts else 0, json.dumps(atts)))
                    indexed += 1
            con.commit()
        finally:
            con.close()
        return jsonify({"ok": True, "indexed": indexed})
    except FileNotFoundError as e:
        return jsonify({"ok": False, "error": str(e)}), 503
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@email_bp.route("/api/email2/attachments/agent-files", methods=["GET"])
def api_agent_files():
    """List files produced by agents/scaffold runs under dashboard/artifacts/.
    Privacy: .private-inbound and .vault_meta are never listed."""
    import mimetypes
    q = (request.args.get("q") or "").strip().lower()
    base = os.path.realpath(ARTIFACTS_DIR)
    files = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in _DENY_ARTIFACT_DIRS]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, base)
            if any(seg in _DENY_ARTIFACT_DIRS for seg in rel.split(os.sep)):
                continue
            if q and q not in rel.lower():
                continue
            try:
                st = os.stat(full)
            except OSError:
                continue
            files.append({
                "name": fn, "rel": rel.replace(os.sep, "/"),
                "project_id": rel.split(os.sep)[0],
                "size": st.st_size, "mtime": st.st_mtime,
                "mime": mimetypes.guess_type(fn)[0] or "application/octet-stream",
            })
    files.sort(key=lambda f: f["mtime"], reverse=True)
    return jsonify({"files": files[:500]})


@email_bp.route("/api/email2/attachments/from-bin", methods=["POST"])
def api_attachment_from_bin():
    """Stage a Baza Bin file as an outgoing attachment. Body: {token}.
    Returns the same shape as /api/email2/attachments/upload."""
    import mimetypes, shutil
    try:
        from dashboard import bin_store
    except ImportError:
        import bin_store
    body = request.get_json(silent=True) or {}
    src = bin_store.resolve_token((body.get("token") or "").strip())
    if not src:
        return jsonify({"ok": False, "error": "invalid bin token"}), 404
    # bin_store stores files under a timestamp-prefixed name (see bin_store.add_file);
    # the original filename lives in the bin_files row, not the on-disk basename.
    _binitem = bin_store.get_by_stored_path(src)
    orig_name = _binitem["name"] if _binitem else os.path.basename(src)
    token = uuid.uuid4().hex
    safe = re.sub(r'[^\w.\- ()]', "_", os.path.basename(orig_name))[:160] or "file"
    d = os.path.join(OUTBOX_DIR, token)
    os.makedirs(d, exist_ok=True)
    dest = os.path.join(d, safe)
    shutil.copy2(src, dest)
    size = os.path.getsize(dest)
    if size > _MAX_ATTACH_BYTES:
        shutil.rmtree(d, ignore_errors=True)
        return jsonify({"ok": False, "error": "file exceeds the 25 MB limit"}), 400
    mime = mimetypes.guess_type(safe)[0] or "application/octet-stream"
    return jsonify({"ok": True, "token": token, "filename": safe, "size": size, "mime": mime})


@email_bp.route("/api/email2/sync", methods=["POST"])
def api_sync():
    """Pull recent unread + last N threads from Gmail into local cache."""
    body = request.get_json(silent=True) or {}
    max_threads = int(body.get("max", 80))
    label = body.get("label", "INBOX")
    try:
        acc = _pick_account(_req_account_id())
        acc_id = acc["id"] if acc else None
        svc = _gmail(acc_id)
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
                            labels, is_unread, is_starred, account_id)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', 'normal', ?, ?, ?, ?)""",
                        (str(uuid.uuid4()), head["id"], tid, hdrs.get("From", ""),
                         hdrs.get("To", ""), hdrs.get("Subject", ""),
                         t.get("snippet", ""), "", hdrs.get("Date", ""),
                         ",".join(labels), 1 if "UNREAD" in labels else 0,
                         1 if "STARRED" in labels else 0, acc_id)
                    )
                    new_count += 1
            con.commit()
        finally:
            con.close()
        return jsonify({"ok": True, "scanned": len(threads), "new": new_count})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def _resolve_attachments(refs):
    """refs: list of server-side refs -> list of {filename, mimetype, data}. Raises ValueError on bad ref."""
    out, total = [], 0
    for ref in refs or []:
        t = ref.get("type")
        if t in ("invoice_pdf", "quote_pdf", "estimate_pdf"):
            from app import render_ahb_doc_pdf  # deferred import avoids circular import
            kind = t.replace("_pdf", "")
            doc_id = ref.get(kind + "_id") or ref.get("id")
            fn, mime, data = render_ahb_doc_pdf(kind, doc_id)
        elif t == "artifact":
            pid = str(ref.get("project_id") or "")
            rel = str(ref.get("path") or "")
            base = os.path.realpath(os.path.join(ARTIFACTS_DIR, pid))
            full = os.path.realpath(os.path.join(base, rel))
            if not (full == base or full.startswith(base + os.sep)):
                raise ValueError("invalid artifact path")
            if any(seg in full.split(os.sep) for seg in _DENY_ARTIFACT_DIRS):
                raise ValueError("artifact is private and cannot be shared")
            if not os.path.isfile(full):
                raise ValueError("artifact not found")
            with open(full, "rb") as f:
                data = f.read()
            fn = os.path.basename(full)
            import mimetypes
            mime = mimetypes.guess_type(fn)[0] or "application/octet-stream"
        elif t == "upload":
            import mimetypes
            token = re.sub(r'[^0-9a-f]', "", str(ref.get("token") or ""))[:32]
            d = os.path.join(OUTBOX_DIR, token)
            if not token or not os.path.isdir(d):
                raise ValueError("upload not found")
            files = [x for x in os.listdir(d) if os.path.isfile(os.path.join(d, x))]
            if not files:
                raise ValueError("upload not found")
            full = os.path.join(d, files[0])
            with open(full, "rb") as f:
                data = f.read()
            fn = os.path.basename(full)
            mime = mimetypes.guess_type(fn)[0] or "application/octet-stream"
        else:
            raise ValueError(f"unknown attachment type: {t}")
        total += len(data)
        if total > _MAX_ATTACH_BYTES:
            raise ValueError("attachments exceed the 25 MB limit")
        out.append({"filename": fn, "mimetype": mime, "data": data})
    return out


def _cleanup_uploads(refs):
    """Delete staged upload dirs for the given send refs (best-effort)."""
    import shutil
    for ref in refs or []:
        if (ref or {}).get("type") == "upload":
            token = re.sub(r'[^0-9a-f]', "", str(ref.get("token") or ""))[:32]
            if token:
                shutil.rmtree(os.path.join(OUTBOX_DIR, token), ignore_errors=True)


def _mime_message(to: str, subject: str, body: str,
                  cc: str = "", bcc: str = "",
                  in_reply_to: str = "", references: str = "",
                  from_addr: str = "", attachments=None) -> str:
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(body, "plain", _charset="utf-8"))
    html_body = "<pre style='font-family:inherit;white-space:pre-wrap'>" + \
                body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") + \
                "</pre>"
    alt.attach(MIMEText(html_body, "html", _charset="utf-8"))

    if attachments:
        msg = MIMEMultipart("mixed")
        msg.attach(alt)
        for a in attachments:
            maintype, _, subtype = (a.get("mimetype") or "application/octet-stream").partition("/")
            part = MIMEApplication(a["data"], _subtype=subtype or "octet-stream")
            part.add_header("Content-Disposition", "attachment", filename=a["filename"])
            msg.attach(part)
    else:
        msg = alt

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
        attach_objs = _resolve_attachments(data.get("attachments"))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except LookupError as e:
        return jsonify({"ok": False, "error": str(e)}), 404

    try:
        svc = _gmail(_req_account_id())
        raw = _mime_message(to, subject, body, cc=cc, bcc=bcc,
                            in_reply_to=in_reply_to, references=references,
                            attachments=attach_objs)
        send_body = {"raw": raw}
        if thread_id:
            send_body["threadId"] = thread_id
        result = svc.users().messages().send(userId="me", body=send_body).execute()
        _cleanup_uploads(data.get("attachments"))
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
        acct = _req_account_id()
        base = """SELECT e.thread_id, e.gmail_id, e.subject, e.from_addr, e.body_snippet,
                         e.received_at, e.labels, e.is_unread, e.is_starred, e.account_id,
                         a.email AS account_email, bm25(emails_fts) AS rank
                  FROM emails_fts JOIN emails e ON e.gmail_id = emails_fts.gmail_id
                  LEFT JOIN email_accounts a ON a.id = e.account_id
                  WHERE emails_fts MATCH ?"""
        params = [f'"{safe}"']
        if acct and acct != "ALL":
            base += " AND (e.account_id = ? OR e.account_id IS NULL)"
            params.append(acct)
        base += " ORDER BY rank LIMIT ?"
        params.append(limit)
        rows = con.execute(base, params).fetchall()
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
                "account_id": d["account_id"],
                "account_email": d["account_email"] or "",
                "rank": d["rank"],
            })
        return jsonify({"results": out, "query": q})
    finally:
        con.close()


@email_bp.route("/api/email2/contacts/suggest", methods=["GET"])
def api_contact_suggest():
    """Autocomplete recipients from AHB clients + email history."""
    q = (request.args.get("q") or "").strip().lower()
    if len(q) < 2:
        return jsonify({"contacts": []})
    out = []
    seen = set()
    con = _conn()
    try:
        # 1) AHB client address book — surfaced first so clients are easy to pick.
        try:
            crows = con.execute(
                """SELECT name, email, company FROM ahb_clients
                   WHERE email IS NOT NULL AND email != ''
                     AND (LOWER(email) LIKE ? OR LOWER(name) LIKE ? OR LOWER(COALESCE(company,'')) LIKE ?)
                   ORDER BY name LIMIT 12""",
                (f"%{q}%", f"%{q}%", f"%{q}%")
            ).fetchall()
            for r in crows:
                addr = (r["email"] or "").strip()
                key = addr.lower()
                if not addr or key in seen:
                    continue
                seen.add(key)
                nm = (r["name"] or "").strip()
                out.append({"name": nm, "email": addr,
                            "raw": f"{nm} <{addr}>" if nm else addr,
                            "source": "client", "company": (r["company"] or "").strip()})
        except Exception:
            pass  # ahb_clients may be absent in some deployments
        # 2) People we've emailed with before (from message history).
        rows = con.execute(
            """SELECT from_addr, COUNT(*) AS n FROM emails
               WHERE LOWER(from_addr) LIKE ? GROUP BY from_addr ORDER BY n DESC LIMIT 12""",
            (f"%{q}%",)
        ).fetchall()
    finally:
        con.close()
    for r in rows:
        name, addr = parseaddr(r["from_addr"] or "")
        if not addr or addr.lower() in seen:
            continue
        seen.add(addr.lower())
        out.append({"name": name, "email": addr, "raw": r["from_addr"],
                    "source": "history", "count": r["n"]})
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


_ACCESS_DENIED_HINT = (
    "Google blocked the sign-in (access_denied). If the OAuth app is in Testing "
    "mode, this Gmail address must be added as a test user first: Google Cloud "
    "Console → APIs & Services → OAuth consent screen → Audience → Test users. "
    "(Project: baza-empire.) Then retry."
)


def _register_account(creds, label: Optional[str]) -> tuple[str, str]:
    """Persist credentials + upsert email_accounts. Returns (email, account_id)."""
    import googleapiclient.discovery
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
    return email, acc_id


def _finish_oauth(flow_id: str, code: str) -> dict:
    """Exchange the auth code and register the account. Updates _oauth_flows."""
    entry = _oauth_flows.get(flow_id)
    if not entry or "flow" not in entry:
        return {"status": "failed", "error": "unknown or expired flow"}
    try:
        flow = entry["flow"]
        flow.fetch_token(code=code)
        email, acc_id = _register_account(flow.credentials, entry.get("label"))
        result = {"status": "done", "email": email, "account_id": acc_id}
    except Exception as e:
        msg = str(e)
        if "access_denied" in msg:
            msg = _ACCESS_DENIED_HINT
        result = {"status": "failed", "error": msg}
    entry.update(result)
    return result


@email_bp.route("/api/email2/accounts/add/start", methods=["POST"])
def api_accounts_add_start():
    """Begin an OAuth flow. Returns the consent URL the user must open.

    Two ways to complete:
    - Browser on baza (or SSH tunnel to :8888): Google redirects to
      /api/email2/oauth/callback and the account is saved automatically.
    - Remote browser: the localhost redirect fails to load — the user copies the
      URL from the address bar and pastes it into the modal (→ /add/finish).
    """
    if not os.path.exists(CREDENTIALS_PATH):
        return jsonify({
            "ok": False,
            "error": f"OAuth client secret not found at {CREDENTIALS_PATH}"
        }), 500
    from google_auth_oauthlib.flow import Flow
    data = request.get_json(silent=True) or {}
    label = (data.get("label") or "").strip() or None
    flow_id = secrets_token()

    flow = Flow.from_client_secrets_file(
        CREDENTIALS_PATH, scopes=SCOPES, redirect_uri=OAUTH_REDIRECT_URI
    )
    auth_url, state = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent"
    )
    # Drop flows older than an hour so the dict can't grow unbounded.
    cutoff = time.time() - 3600
    for fid in [f for f, v in _oauth_flows.items() if v.get("created", 0) < cutoff]:
        _oauth_flows.pop(fid, None)
    _oauth_flows[flow_id] = {
        "status": "pending", "flow": flow, "label": label,
        "state": state, "created": time.time(),
    }
    return jsonify({"ok": True, "flow_id": flow_id, "auth_url": auth_url,
                    "redirect_uri": OAUTH_REDIRECT_URI})


@email_bp.route("/api/email2/oauth/callback", methods=["GET"])
def api_oauth_callback():
    """Loopback landing for the add-account flow (browser on baza / tunnel)."""
    state = request.args.get("state", "")
    code = request.args.get("code", "")
    error = request.args.get("error", "")
    flow_id = next(
        (f for f, v in _oauth_flows.items() if v.get("state") == state), None
    )

    def _page(title: str, body: str) -> tuple[str, int]:
        return (f"<html><body style='font-family:sans-serif;background:#0a0a1a;"
                f"color:#e0e0e0;padding:40px'><h2>{title}</h2><p>{body}</p>"
                f"</body></html>"), 200

    if not flow_id:
        return _page("⚠ Unknown OAuth flow",
                     "This flow expired or was already completed. "
                     "Start again from the Mail tab.")
    if error:
        msg = _ACCESS_DENIED_HINT if error == "access_denied" else error
        _oauth_flows[flow_id].update({"status": "failed", "error": msg})
        return _page("⚠ Google sign-in failed", msg)
    if not code:
        return _page("⚠ Missing authorization code", "Try the flow again.")
    result = _finish_oauth(flow_id, code)
    if result["status"] == "done":
        return _page("✅ Account added",
                     f"<strong>{result['email']}</strong> is connected. "
                     f"Return to the Mail tab.")
    return _page("⚠ Could not add account", result.get("error", "unknown error"))


@email_bp.route("/api/email2/accounts/add/finish", methods=["POST"])
def api_accounts_add_finish():
    """Manual completion: accepts the pasted redirect URL (or bare code).
    Body: {flow_id, redirect_url}"""
    data = request.get_json(silent=True) or {}
    flow_id = (data.get("flow_id") or "").strip()
    raw = (data.get("redirect_url") or "").strip()
    if not flow_id or not raw:
        return jsonify({"ok": False, "error": "missing flow_id or redirect_url"}), 400
    code = raw
    if "://" in raw or "?" in raw:
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(raw).query)
        if qs.get("error"):
            err = qs["error"][0]
            msg = _ACCESS_DENIED_HINT if err == "access_denied" else err
            _oauth_flows.get(flow_id, {}).update({"status": "failed", "error": msg})
            return jsonify({"ok": False, "error": msg}), 400
        code = (qs.get("code") or [""])[0]
    if not code:
        return jsonify({"ok": False, "error": "no authorization code found in the pasted URL"}), 400
    result = _finish_oauth(flow_id, code)
    if result["status"] == "done":
        return jsonify({"ok": True, **result})
    return jsonify({"ok": False, "error": result.get("error", "unknown")}), 400


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
    # Strip non-serializable internals (the Flow object itself).
    safe = {k: v for k, v in info.items()
            if k in ("status", "email", "account_id", "error")}
    return jsonify(safe)


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
def _send_unsub_mailto(svc, mailto_uri: str) -> dict:
    """Send a plain unsubscribe email per the List-Unsubscribe mailto: URI.
    mailto syntax: mailto:address?subject=...&body=..."""
    from urllib.parse import urlparse, parse_qs, unquote
    p = urlparse(mailto_uri)
    to = p.path or ""
    qs = parse_qs(p.query)
    subject = (qs.get("subject", ["unsubscribe"])[0]) or "unsubscribe"
    body = unquote(qs.get("body", ["unsubscribe"])[0]) or "unsubscribe"
    msg = MIMEText(body, "plain", _charset="utf-8")
    msg["To"] = to
    msg["Subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    result = svc.users().messages().send(userId="me", body={"raw": raw}).execute()
    return {"to": to, "subject": subject, "gmail_id": result.get("id")}


def _try_unsub_http(url: str) -> dict:
    """Attempt the RFC 8058 one-click POST. Uses requests (better redirect/SSL
    handling than urllib). On TLS/SSL failure of an https URL, retries http://.
    Returns {ok, status?, final_url?, fell_back?, error?}."""
    import requests
    body = "List-Unsubscribe=One-Click"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        # Some senders 403 on default urllib UA; mimic a normal browser.
        "User-Agent": "Mozilla/5.0 (compatible; BazaMail/1.0 unsubscribe)",
    }
    attempts: list[dict] = []

    def _attempt(u: str, allow_get_fallback: bool) -> dict:
        # First try POST with follow-redirects on
        try:
            r = requests.post(u, data=body, headers=headers, timeout=15,
                              allow_redirects=True)
            ok = 200 <= r.status_code < 400
            res = {"method": "POST", "url": u, "status": r.status_code,
                   "final_url": r.url, "ok": ok}
            if ok:
                return res
            # Some senders accept only GET on the link (older RFC 2369 style)
            if allow_get_fallback and r.status_code in (400, 404, 405):
                try:
                    rg = requests.get(u, headers=headers, timeout=15,
                                      allow_redirects=True)
                    res2 = {"method": "GET", "url": u, "status": rg.status_code,
                            "final_url": rg.url, "ok": 200 <= rg.status_code < 400}
                    return res2
                except Exception as e2:
                    res["get_error"] = str(e2)
            return res
        except requests.exceptions.SSLError as e:
            return {"method": "POST", "url": u, "ok": False,
                    "error": f"SSL: {e}", "ssl_error": True}
        except Exception as e:
            return {"method": "POST", "url": u, "ok": False, "error": str(e)}

    a = _attempt(url, allow_get_fallback=True)
    attempts.append(a)
    if a.get("ok"):
        return {"ok": True, "status": a.get("status"), "final_url": a.get("final_url"),
                "method": a.get("method"), "fell_back": False, "attempts": attempts}

    # SSL handshake failure on https — try the http equivalent.
    if a.get("ssl_error") and url.startswith("https://"):
        http_url = "http://" + url[len("https://"):]
        b = _attempt(http_url, allow_get_fallback=True)
        attempts.append(b)
        if b.get("ok"):
            return {"ok": True, "status": b.get("status"), "final_url": b.get("final_url"),
                    "method": b.get("method"), "fell_back": True,
                    "fallback_kind": "ssl_to_http", "attempts": attempts}

    return {"ok": False, "error": a.get("error") or f"HTTP {a.get('status')}",
            "attempts": attempts}


@email_bp.route("/api/email2/cleanup/unsubscribe", methods=["POST"])
def api_cleanup_unsubscribe():
    """Best-effort unsubscribe. Order:
      1) RFC 8058 one-click POST to https:// (with redirects)
      2) GET fallback if the POST 405/404s
      3) https → http retry on SSL handshake failure
      4) mailto: fallback (sends a 1-line email via Gmail)
    Body: {gmail_id, prefer_mailto?, account?}"""
    data = request.get_json(silent=True) or {}
    gid = data.get("gmail_id")
    if not gid:
        return jsonify({"ok": False, "error": "missing gmail_id"}), 400
    prefer_mailto = bool(data.get("prefer_mailto"))
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
        mailto = next((u for u in urls if u.startswith("mailto:")), "")
        if not http_url and not mailto:
            return jsonify({"ok": False, "error": "no unsubscribe targets in header"}), 400

        # Allow callers to skip the http path entirely (e.g. previously failed)
        if prefer_mailto and mailto:
            sent = _send_unsub_mailto(svc, mailto)
            return jsonify({"ok": True, "via": "mailto", **sent})

        http_result = None
        if http_url:
            http_result = _try_unsub_http(http_url)
            if http_result.get("ok"):
                return jsonify({"ok": True, "via": "http", **http_result})

        # http failed (or no http URL) — try mailto
        if mailto:
            try:
                sent = _send_unsub_mailto(svc, mailto)
                return jsonify({
                    "ok": True, "via": "mailto",
                    "http_failed": http_result if http_result else None,
                    **sent,
                })
            except Exception as e:
                return jsonify({
                    "ok": False,
                    "error": f"both http and mailto failed; mailto: {e}",
                    "http_result": http_result, "mailto": mailto,
                }), 502

        return jsonify({
            "ok": False, "error": "unsubscribe attempts failed",
            "http_result": http_result, "mailto": mailto or None,
        }), 502
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
