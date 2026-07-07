# Phantom Browser — AI Web Crawler Kit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A central Playwright service on baza :8100 giving all 9 agents Firecrawl-style verbs (scrape/search/map/crawl/extract) plus interactive, optionally logged-in browser sessions with a Telegram write-approval gate.

**Architecture:** New `browser/` package (FastAPI + async Playwright, own systemd unit `baza-phantom-browser.service`, state in `dashboard/phantom_browser.db`), a SearXNG docker container on :8181 for search, and six thin skills in `skills/shared/` that all agents reach through the existing FTS skill scaffold and plan→act→observe loop.

**Tech Stack:** Python 3.12, FastAPI/uvicorn, Playwright (headless Chromium), trafilatura + markdownify, httpx, SQLite (WAL), SearXNG (docker), local Ollama for extraction.

**Spec:** `docs/superpowers/specs/2026-07-07-phantom-browser-crawler-kit-design.md`

## Global Constraints

- Framework root: `/home/switchhacker/baza-empire/agent-framework-v3` (call it `$FW`). All relative paths below are under it.
- **NO manual `git commit`/`git push`** — the `claw-auto-git` timer commits hourly (house rule). Plan steps therefore have no commit steps; just save files.
- **Local-first HARD rule:** `/extract` calls local Ollama only (`OLLAMA_URL` default `http://localhost:11434`). No cloud LLM calls anywhere in this feature.
- Run tests with `$FW/venv/bin/python -m pytest` (pytest.ini already sets `testpaths = tests`). Mark network/Chromium tests `@pytest.mark.integration` (marker exists).
- Skills contract: args via `SKILL_ARGS` env (JSON), output via stdout, `SKILL_META` dict literal at top of file.
- Service idiom: systemd system unit, `User=switchhacker`, `Environment=PYTHONPATH=$FW`, `EnvironmentFile=$FW/configs/secrets.env`.
- Env vars introduced (all with code defaults, none secret): `PHANTOM_BROWSER_URL=http://localhost:8100`, `SEARXNG_URL=http://localhost:8181`, `PB_EXTRACT_MODEL=glm-4.7-flash`, `OLLAMA_URL=http://localhost:11434`, `PB_PUBLIC_URL=http://100.127.118.103:8100`, `PHANTOM_BROWSER_DB` (test override), `PB_PROFILES_DIR` (test override).
- Approval timeout: 300 s, silence = denied. Idle session TTL: 600 s. Max contexts: 4. Default crawl cap: 50 pages.
- Telegram send: `core.telegram_fmt.post_html(token, chat_id, text)` with `TELEGRAM_SIMON_BATELY` + `SERGE_CHAT_ID` from env (secrets.env is injected by the unit).
- Restarting the service: `sudo systemctl restart baza-phantom-browser.service`. It does NOT exist until Task 12.
- Append session-log entries per house rules as tasks complete.

---

### Task 1: Runtime deps + `browser/` package + state DB

**Files:**
- Create: `browser/__init__.py` (empty)
- Create: `browser/db.py`
- Create: `tests/browser/__init__.py` (empty), `tests/browser/conftest.py`
- Test: `tests/browser/test_db.py`
- Modify: `requirements.txt` (append)

**Interfaces:**
- Produces: `browser.db` module — `init()`, `connect()`, `create_job(root_url, params) -> int`, `get_job(job_id) -> dict|None`, `set_job_status(job_id, status, error=None)`, `add_page(job_id, url, title, markdown, status="ok", error=None)`, `job_pages(job_id) -> list[dict]`, `requeue_running() -> list[int]`, `create_approval(session_id, action, description, token) -> int`, `get_approval(approval_id) -> dict|None`, `decide_approval(approval_id, status)`, `expire_stale(max_age=300) -> int`, `cache_get(url, ttl=900) -> dict|None`, `cache_put(url, payload)`.
- DB path resolved per-call from `PHANTOM_BROWSER_DB` env or `$FW/dashboard/phantom_browser.db`.

- [ ] **Step 1: Install runtime dependencies into the venv**

```bash
cd /home/switchhacker/baza-empire/agent-framework-v3
venv/bin/pip install playwright trafilatura markdownify
venv/bin/python -m playwright install chromium
venv/bin/python -c "import playwright, trafilatura, markdownify, fastapi, uvicorn; print('deps ok')"
```
Expected: `deps ok`. (fastapi/uvicorn are already in the venv; Chromium download is ~170 MB.)

- [ ] **Step 2: Append to `requirements.txt`**

Append these lines (fastapi/uvicorn were used but never pinned — fix that too):

```
fastapi>=0.111.0
uvicorn>=0.30.0
playwright>=1.45.0
trafilatura>=1.9.0
markdownify>=0.13.0
```

- [ ] **Step 3: Write `tests/browser/conftest.py`**

```python
import os
import sys

# Make the framework root importable so `import browser.*` resolves
# regardless of pytest's import mode.
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
```

- [ ] **Step 4: Write the failing tests** — `tests/browser/test_db.py`

```python
import json


def test_init_and_job_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("PHANTOM_BROWSER_DB", str(tmp_path / "pb.db"))
    from browser import db
    db.init()
    jid = db.create_job("https://example.com", {"max_pages": 5})
    job = db.get_job(jid)
    assert job["status"] == "pending"
    assert json.loads(job["params"])["max_pages"] == 5
    db.set_job_status(jid, "running")
    db.add_page(jid, "https://example.com/", "Example", "# Example")
    db.add_page(jid, "https://example.com/bad", None, None, status="error", error="boom")
    db.set_job_status(jid, "done")
    job = db.get_job(jid)
    assert job["status"] == "done" and job["finished_at"] is not None
    pages = db.job_pages(jid)
    assert len(pages) == 2 and pages[0]["title"] == "Example"


def test_requeue_running(tmp_path, monkeypatch):
    monkeypatch.setenv("PHANTOM_BROWSER_DB", str(tmp_path / "pb.db"))
    from browser import db
    db.init()
    j1 = db.create_job("https://a.com", {})
    db.set_job_status(j1, "running")
    j2 = db.create_job("https://b.com", {})
    assert db.requeue_running() == [j1]
    assert db.get_job(j1)["status"] == "pending"
    assert db.get_job(j2)["status"] == "pending"


def test_approvals_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv("PHANTOM_BROWSER_DB", str(tmp_path / "pb.db"))
    from browser import db
    db.init()
    aid = db.create_approval("sess1", {"op": "click", "index": 3}, "click Send", "tok123")
    a = db.get_approval(aid)
    assert a["status"] == "pending" and a["token"] == "tok123"
    assert json.loads(a["action"])["index"] == 3
    db.decide_approval(aid, "approved")
    assert db.get_approval(aid)["status"] == "approved"
    assert db.get_approval(aid)["decided_at"] is not None


def test_expire_stale(tmp_path, monkeypatch):
    monkeypatch.setenv("PHANTOM_BROWSER_DB", str(tmp_path / "pb.db"))
    from browser import db
    db.init()
    aid = db.create_approval("s", {"op": "click", "index": 0}, "d", "t")
    with db._conn() as c:
        c.execute("UPDATE approvals SET created_at = created_at - 9999 WHERE id=?", (aid,))
    assert db.expire_stale(max_age=300) == 1
    assert db.get_approval(aid)["status"] == "expired"


def test_cache_roundtrip_and_ttl(tmp_path, monkeypatch):
    monkeypatch.setenv("PHANTOM_BROWSER_DB", str(tmp_path / "pb.db"))
    from browser import db
    db.init()
    assert db.cache_get("https://x.com") is None
    db.cache_put("https://x.com", {"markdown": "hi", "title": "X"})
    assert db.cache_get("https://x.com")["title"] == "X"
    assert db.cache_get("https://x.com", ttl=0) is None
    db.cache_put("https://x.com", {"markdown": "hi2", "title": "X2"})  # upsert
    assert db.cache_get("https://x.com")["title"] == "X2"
```

- [ ] **Step 5: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/browser/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'browser'` (or ImportError).

- [ ] **Step 6: Write `browser/__init__.py` (empty) and `browser/db.py`**

```python
"""SQLite state for the Phantom Browser service (:8100): crawl jobs + pages,
write-gate approvals, and the short-TTL page cache.

DB lives at dashboard/phantom_browser.db (override: PHANTOM_BROWSER_DB env).
House idiom (see core/cron_health_db.py): WAL, Row factory, 5s timeout,
context-managed commit, idempotent init().
"""
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

_FRAMEWORK_DIR = Path(__file__).resolve().parent.parent

SCHEMA = """
CREATE TABLE IF NOT EXISTS crawl_jobs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  root_url    TEXT NOT NULL,
  params      TEXT NOT NULL DEFAULT '{}',
  status      TEXT NOT NULL DEFAULT 'pending',
  error       TEXT,
  created_at  REAL NOT NULL,
  finished_at REAL
);
CREATE TABLE IF NOT EXISTS crawl_pages (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id     INTEGER NOT NULL REFERENCES crawl_jobs(id),
  url        TEXT NOT NULL,
  title      TEXT,
  markdown   TEXT,
  status     TEXT NOT NULL DEFAULT 'ok',
  error      TEXT,
  fetched_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_crawl_pages_job ON crawl_pages(job_id);
CREATE TABLE IF NOT EXISTS approvals (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id  TEXT NOT NULL,
  action      TEXT NOT NULL,
  description TEXT NOT NULL,
  token       TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'pending',
  created_at  REAL NOT NULL,
  decided_at  REAL
);
CREATE TABLE IF NOT EXISTS page_cache (
  url        TEXT PRIMARY KEY,
  fetched_at REAL NOT NULL,
  payload    TEXT NOT NULL
);
"""


def _db_path() -> Path:
    return Path(
        os.environ.get("PHANTOM_BROWSER_DB")
        or str(_FRAMEWORK_DIR / "dashboard" / "phantom_browser.db")
    )


def connect() -> sqlite3.Connection:
    p = _db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def _conn():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with _conn() as c:
        c.executescript(SCHEMA)


# ── crawl jobs ────────────────────────────────────────────────────────────

def create_job(root_url: str, params: dict) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO crawl_jobs (root_url, params, created_at) VALUES (?,?,?)",
            (root_url, json.dumps(params), time.time()),
        )
        return cur.lastrowid


def get_job(job_id: int):
    with _conn() as c:
        row = c.execute("SELECT * FROM crawl_jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None


def set_job_status(job_id: int, status: str, error: str | None = None) -> None:
    finished = time.time() if status in ("done", "error") else None
    with _conn() as c:
        c.execute(
            "UPDATE crawl_jobs SET status=?, error=?, finished_at=COALESCE(?, finished_at) WHERE id=?",
            (status, error, finished, job_id),
        )


def add_page(job_id: int, url: str, title, markdown, status: str = "ok", error=None) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO crawl_pages (job_id, url, title, markdown, status, error, fetched_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (job_id, url, title, markdown, status, error, time.time()),
        )


def job_pages(job_id: int) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM crawl_pages WHERE job_id=? ORDER BY id", (job_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def requeue_running() -> list[int]:
    """On service startup: any job left 'running' by a crash/restart goes back
    to 'pending' so the server can relaunch it."""
    with _conn() as c:
        rows = c.execute("SELECT id FROM crawl_jobs WHERE status='running'").fetchall()
        ids = [r["id"] for r in rows]
        if ids:
            c.execute(
                f"UPDATE crawl_jobs SET status='pending' WHERE id IN ({','.join('?'*len(ids))})",
                ids,
            )
        return ids


# ── approvals (write gate) ────────────────────────────────────────────────

def create_approval(session_id: str, action: dict, description: str, token: str) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO approvals (session_id, action, description, token, created_at)"
            " VALUES (?,?,?,?,?)",
            (session_id, json.dumps(action), description, token, time.time()),
        )
        return cur.lastrowid


def get_approval(approval_id: int):
    with _conn() as c:
        row = c.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        return dict(row) if row else None


def decide_approval(approval_id: int, status: str) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE approvals SET status=?, decided_at=? WHERE id=?",
            (status, time.time(), approval_id),
        )


def expire_stale(max_age: int = 300) -> int:
    """Pending approvals older than max_age seconds become 'expired' (= denied).
    Returns how many were expired."""
    with _conn() as c:
        cur = c.execute(
            "UPDATE approvals SET status='expired', decided_at=? "
            "WHERE status='pending' AND created_at < ?",
            (time.time(), time.time() - max_age),
        )
        return cur.rowcount


# ── page cache ────────────────────────────────────────────────────────────

def cache_get(url: str, ttl: int = 900):
    with _conn() as c:
        row = c.execute("SELECT * FROM page_cache WHERE url=?", (url,)).fetchone()
        if not row or time.time() - row["fetched_at"] > ttl:
            return None
        return json.loads(row["payload"])


def cache_put(url: str, payload: dict) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO page_cache (url, fetched_at, payload) VALUES (?,?,?) "
            "ON CONFLICT(url) DO UPDATE SET fetched_at=excluded.fetched_at, payload=excluded.payload",
            (url, time.time(), json.dumps(payload)),
        )
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/browser/test_db.py -v`
Expected: 5 passed.

---

### Task 2: `browser/page_to_md.py` — rendered HTML → markdown

**Files:**
- Create: `browser/page_to_md.py`
- Test: `tests/browser/test_page_to_md.py`

**Interfaces:**
- Produces: `page_to_md(html: str, url: str, max_chars: int = 8000) -> dict` with keys `markdown, title, description, links (list[str], absolute, deduped, ≤100), truncated (bool)`.

- [ ] **Step 1: Write the failing tests** — `tests/browser/test_page_to_md.py`

```python
from browser.page_to_md import page_to_md

HTML = """<!doctype html><html><head>
<title>  Widget   Prices </title>
<meta name="description" content="Best widget prices in PA">
<script>alert('evil')</script><style>.x{color:red}</style>
</head><body>
<nav><a href="/nav1">Nav</a></nav>
<article><h1>Widget Prices</h1>
<p>The blue widget costs $5. The red widget costs $9.</p>
<a href="/products/blue">Blue widget</a>
<a href="https://other.com/red#frag">Red widget</a>
<a href="/products/blue">Blue again (dup)</a>
</article></body></html>"""


def test_extracts_markdown_and_title():
    out = page_to_md(HTML, "https://shop.example.com/list")
    assert "Widget Prices" in out["markdown"]
    assert "$5" in out["markdown"]
    assert "alert('evil')" not in out["markdown"]
    assert out["title"] == "Widget Prices"


def test_links_absolute_and_deduped():
    out = page_to_md(HTML, "https://shop.example.com/list")
    assert "https://shop.example.com/products/blue" in out["links"]
    assert "https://other.com/red" in out["links"]
    assert out["links"].count("https://shop.example.com/products/blue") == 1


def test_truncation():
    big = "<html><body><article><p>" + ("word " * 5000) + "</p></article></body></html>"
    out = page_to_md(big, "https://x.com", max_chars=500)
    assert len(out["markdown"]) <= 500
    assert out["truncated"] is True


def test_garbage_html_does_not_crash():
    out = page_to_md("<<<>>>not html at all", "https://x.com")
    assert isinstance(out["markdown"], str)
    assert out["links"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/browser/test_page_to_md.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'browser.page_to_md'`.

- [ ] **Step 3: Write `browser/page_to_md.py`**

```python
"""Rendered HTML → clean markdown + metadata. trafilatura extracts the main
content; markdownify is the fallback for pages trafilatura can't parse."""
import re
from urllib.parse import urljoin, urldefrag

import trafilatura
from markdownify import markdownify


def page_to_md(html: str, url: str, max_chars: int = 8000) -> dict:
    md = None
    try:
        md = trafilatura.extract(
            html, url=url, output_format="markdown",
            include_links=True, include_tables=True,
        )
    except Exception:
        md = None
    if not md:
        body = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", html)
        try:
            md = markdownify(body, strip=["img"]) or ""
        except Exception:
            md = re.sub(r"(?s)<[^>]+>", " ", body)
        md = re.sub(r"\n{3,}", "\n\n", md)
        md = re.sub(r"[ \t]{2,}", " ", md).strip()

    title, description = "", ""
    try:
        meta = trafilatura.extract_metadata(html, default_url=url)
        if meta:
            title = (meta.title or "").strip()
            description = (meta.description or "").strip()
    except Exception:
        pass
    if not title:
        m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
        if m:
            title = re.sub(r"\s+", " ", m.group(1)).strip()

    links: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"""(?is)<a[^>]+href=["']([^"']+)["']""", html):
        href = m.group(1).strip()
        if href.startswith(("javascript:", "mailto:", "tel:", "#", "data:")):
            continue
        absu, _ = urldefrag(urljoin(url, href))
        if absu.startswith(("http://", "https://")) and absu not in seen:
            seen.add(absu)
            links.append(absu)
        if len(links) >= 100:
            break

    truncated = len(md) > max_chars
    return {
        "markdown": md[:max_chars],
        "title": title,
        "description": description,
        "links": links,
        "truncated": truncated,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/browser/test_page_to_md.py -v`
Expected: 4 passed. (If `test_extracts_markdown_and_title` fails on the title because trafilatura returns the h1, that is acceptable — assert would still pass since both are "Widget Prices". If trafilatura drops the nav link that is fine; links come from raw HTML regex.)

---

### Task 3: `browser/engine.py` — Playwright lifecycle

**Files:**
- Create: `browser/engine.py`
- Test: `tests/browser/test_engine.py`

**Interfaces:**
- Produces: `class Engine(max_contexts=4, domain_delay=1.0)` with `async start()`, `async stop()`, `async render(url, wait_ms=0, screenshot=False) -> {"html","final_url","status","screenshot_path"}`, `async new_context(profile=None)` (profile → `launch_persistent_context` from `profiles_dir()/name`, raises `ValueError` on unknown profile). Module function `profiles_dir() -> Path` (env `PB_PROFILES_DIR` or `browser/profiles`). Constant `UA` (desktop Chrome UA string).
- Screenshots land in `$FW/dashboard/artifacts/browser/`.

- [ ] **Step 1: Write the failing tests** — `tests/browser/test_engine.py`

```python
import asyncio

import pytest

from browser.engine import Engine, profiles_dir


def test_profiles_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("PB_PROFILES_DIR", str(tmp_path / "prof"))
    assert str(profiles_dir()) == str(tmp_path / "prof")


@pytest.mark.integration
def test_render_data_url():
    async def go():
        eng = Engine()
        await eng.start()
        try:
            out = await eng.render(
                "data:text/html,<html><head><title>T1</title></head>"
                "<body><h1>hello-engine</h1></body></html>"
            )
            return out
        finally:
            await eng.stop()

    out = asyncio.run(go())
    assert "hello-engine" in out["html"]
    assert out["screenshot_path"] is None


@pytest.mark.integration
def test_unknown_profile_raises():
    async def go():
        eng = Engine()
        await eng.start()
        try:
            with pytest.raises(ValueError):
                await eng.new_context(profile="no-such-profile")
        finally:
            await eng.stop()

    asyncio.run(go())


def test_polite_wait_spaces_same_domain():
    async def go():
        eng = Engine(domain_delay=0.2)
        import time
        t0 = time.monotonic()
        await eng._polite_wait("https://same.com/a")
        await eng._polite_wait("https://same.com/b")
        return time.monotonic() - t0

    assert asyncio.run(go()) >= 0.2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/browser/test_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'browser.engine'`.

- [ ] **Step 3: Write `browser/engine.py`**

```python
"""Playwright lifecycle: one shared headless Chromium, contexts capped by a
semaphore, per-domain politeness delay, auto-relaunch if Chromium dies."""
import asyncio
import os
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright, Error as PWError

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_FRAMEWORK_DIR = Path(__file__).resolve().parent.parent
SCREENSHOT_DIR = _FRAMEWORK_DIR / "dashboard" / "artifacts" / "browser"


def profiles_dir() -> Path:
    return Path(
        os.environ.get("PB_PROFILES_DIR")
        or str(Path(__file__).resolve().parent / "profiles")
    )


class Engine:
    def __init__(self, max_contexts: int = 4, domain_delay: float = 1.0):
        self._pw = None
        self._browser = None
        self._sem = asyncio.Semaphore(max_contexts)
        self._domain_delay = domain_delay
        self._last_hit: dict[str, float] = {}
        self._relaunch_lock = asyncio.Lock()

    async def start(self) -> None:
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=True)

    async def stop(self) -> None:
        for closer in (self._browser, self._pw):
            try:
                if closer is self._browser and self._browser:
                    await self._browser.close()
                elif closer is self._pw and self._pw:
                    await self._pw.stop()
            except Exception:
                pass
        self._browser = None
        self._pw = None

    async def _ensure_browser(self) -> None:
        if self._browser is not None and self._browser.is_connected():
            return
        async with self._relaunch_lock:
            if self._browser is None or not self._browser.is_connected():
                await self.stop()
                await self.start()

    async def _polite_wait(self, url: str) -> None:
        host = urlparse(url).netloc
        if host:
            last = self._last_hit.get(host, 0.0)
            wait = self._domain_delay - (time.monotonic() - last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_hit[host] = time.monotonic()

    async def new_context(self, profile: str | None = None):
        """Plain contexts come from the shared browser. Profile sessions get a
        persistent context (their own Chromium) rooted at profiles/<name>."""
        await self._ensure_browser()
        if profile:
            pdir = profiles_dir() / profile
            if not pdir.is_dir():
                raise ValueError(f"unknown profile '{profile}' — seed it with browser/login_helper.py")
            return await self._pw.chromium.launch_persistent_context(
                str(pdir), headless=True, user_agent=UA
            )
        return await self._browser.new_context(user_agent=UA)

    async def render(self, url: str, wait_ms: int = 0, screenshot: bool = False) -> dict:
        await self._ensure_browser()
        async with self._sem:
            await self._polite_wait(url)
            ctx = await self._browser.new_context(user_agent=UA)
            try:
                page = await ctx.new_page()
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=8000)
                except PWError:
                    pass  # busy pages never go idle; domcontentloaded is enough
                if wait_ms:
                    await page.wait_for_timeout(min(int(wait_ms), 10000))
                shot = None
                if screenshot:
                    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
                    shot = str(SCREENSHOT_DIR / f"scrape_{int(time.time() * 1000)}.png")
                    await page.screenshot(path=shot, full_page=False)
                return {
                    "html": await page.content(),
                    "final_url": page.url,
                    "status": resp.status if resp else None,
                    "screenshot_path": shot,
                }
            finally:
                await ctx.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/browser/test_engine.py -v`
Expected: 4 passed (2 integration tests launch real Chromium; no network needed — data: URLs only).

---

### Task 4: `browser/server.py` — app skeleton, `/health`, `/scrape`

**Files:**
- Create: `browser/server.py`
- Test: `tests/browser/test_server_scrape.py`

**Interfaces:**
- Consumes: `Engine.render`, `page_to_md`, `db.cache_get/cache_put/init`.
- Produces: FastAPI `app`; module-level `engine = Engine()`; `async do_scrape(url, max_chars=8000, wait_ms=0, screenshot=False, no_cache=False) -> dict` (reused by /search, /crawl, /extract in later tasks); response shape `{"success", "url", "final_url", "status", "title", "description", "markdown", "links", "truncated", "screenshot_path", "cached"}` — on error `{"success": False, "url", "error"}`.
- Test helper pattern for all later server tests: monkeypatch `engine.start/stop` to no-ops and `engine.render` to a fake before creating `TestClient`.

- [ ] **Step 1: Write the failing tests** — `tests/browser/test_server_scrape.py`

```python
import pytest
from fastapi.testclient import TestClient

FAKE_HTML = "<html><head><title>Fake</title></head><body><article><p>fake body text</p><a href='/next'>next</a></article></body></html>"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PHANTOM_BROWSER_DB", str(tmp_path / "pb.db"))
    from browser import server

    async def fake_start(): ...
    async def fake_stop(): ...
    async def fake_render(url, wait_ms=0, screenshot=False):
        return {"html": FAKE_HTML, "final_url": url, "status": 200, "screenshot_path": None}

    monkeypatch.setattr(server.engine, "start", fake_start)
    monkeypatch.setattr(server.engine, "stop", fake_stop)
    monkeypatch.setattr(server.engine, "render", fake_render)
    with TestClient(server.app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_scrape_returns_markdown(client):
    r = client.post("/scrape", json={"url": "https://fake.test/page"})
    body = r.json()
    assert body["success"] is True
    assert "fake body text" in body["markdown"]
    assert body["title"] == "Fake"
    assert body["links"] == ["https://fake.test/next"]
    assert body["cached"] is False


def test_scrape_cache_hit(client):
    client.post("/scrape", json={"url": "https://fake.test/c"})
    r2 = client.post("/scrape", json={"url": "https://fake.test/c"})
    assert r2.json()["cached"] is True


def test_scrape_error_is_structured(client, monkeypatch):
    from browser import server

    async def boom(url, wait_ms=0, screenshot=False):
        raise TimeoutError("nav timeout")

    monkeypatch.setattr(server.engine, "render", boom)
    r = client.post("/scrape", json={"url": "https://fake.test/err", "no_cache": True})
    body = r.json()
    assert body["success"] is False and "nav timeout" in body["error"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/browser/test_server_scrape.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'browser.server'`.

- [ ] **Step 3: Write `browser/server.py`**

```python
"""Phantom Browser — FastAPI service on :8100. Firecrawl-style verbs
(scrape/search/map/crawl/extract) + interactive sessions for baza agents.

Run: venv/bin/uvicorn server:app --host 0.0.0.0 --port 8100
(WorkingDirectory=browser/, PYTHONPATH=framework root — see systemd unit.)
"""
import asyncio
import logging
import os

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from pydantic import BaseModel

try:  # package import (tests) or flat import (uvicorn server:app from browser/)
    from browser import db
    from browser.engine import Engine, UA
    from browser.page_to_md import page_to_md
except ImportError:  # pragma: no cover
    import db
    from engine import Engine, UA
    from page_to_md import page_to_md

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("phantom_browser")

engine = Engine()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    await engine.start()
    yield
    await engine.stop()


app = FastAPI(title="Phantom Browser", version="1.0.0", lifespan=lifespan)


class ScrapeReq(BaseModel):
    url: str
    max_chars: int = 8000
    wait_ms: int = 0
    screenshot: bool = False
    no_cache: bool = False


@app.get("/health")
async def health():
    return {"ok": True, "service": "phantom-browser"}


async def do_scrape(url: str, max_chars: int = 8000, wait_ms: int = 0,
                    screenshot: bool = False, no_cache: bool = False) -> dict:
    cacheable = not screenshot and wait_ms == 0
    if cacheable and not no_cache:
        hit = db.cache_get(url)
        if hit:
            return {**hit, "cached": True}
    r = await engine.render(url, wait_ms=wait_ms, screenshot=screenshot)
    md = page_to_md(r["html"], r["final_url"], max_chars=max_chars)
    out = {
        "success": True, "url": url, "final_url": r["final_url"],
        "status": r["status"], "title": md["title"],
        "description": md["description"], "markdown": md["markdown"],
        "links": md["links"], "truncated": md["truncated"],
        "screenshot_path": r["screenshot_path"], "cached": False,
    }
    if cacheable:
        db.cache_put(url, out)
    return out


@app.post("/scrape")
async def scrape(req: ScrapeReq):
    try:
        return await do_scrape(req.url, req.max_chars, req.wait_ms,
                               req.screenshot, req.no_cache)
    except Exception as e:
        return {"success": False, "url": req.url, "error": f"{type(e).__name__}: {e}"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/browser/test_server_scrape.py -v`
Expected: 4 passed.

---

### Task 5: SearXNG container + `/search`

**Files:**
- Create: `/home/switchhacker/searxng/docker-compose.yml` (outside repo, like nextcloud)
- Create: `/home/switchhacker/searxng/config/settings.yml`
- Modify: `browser/server.py` (add `/search`)
- Test: `tests/browser/test_server_search.py`

**Interfaces:**
- Consumes: `do_scrape` (for `fetch_content`).
- Produces: `POST /search` `{query, n=5, fetch_content=False, max_chars=3000}` → `{"success", "query", "source": "searxng", "results": [{"title","url","snippet"[,"content"]}]}`. Env `SEARXNG_URL` default `http://localhost:8181`.

- [ ] **Step 1: Deploy SearXNG**

`/home/switchhacker/searxng/docker-compose.yml`:

```yaml
services:
  searxng:
    image: searxng/searxng:latest
    container_name: searxng
    ports:
      - "8181:8080"
    volumes:
      - ./config:/etc/searxng
    restart: unless-stopped
```

`/home/switchhacker/searxng/config/settings.yml`:

```yaml
use_default_settings: true
server:
  secret_key: "REPLACE_ME"
  limiter: false
  public_instance: false
search:
  formats:
    - html
    - json
```

Then:

```bash
mkdir -p ~/searxng/config
# (write the two files above)
sed -i "s/REPLACE_ME/$(openssl rand -hex 32)/" ~/searxng/config/settings.yml
cd ~/searxng && docker compose up -d
sleep 8
curl -s "http://localhost:8181/search?q=test&format=json" | head -c 200
```
Expected: JSON starting `{"query": "test", ...` (not an HTML error page, not 403/429).

- [ ] **Step 2: Write the failing tests** — `tests/browser/test_server_search.py`

```python
import pytest
from fastapi.testclient import TestClient

SEARX_JSON = {
    "query": "widgets",
    "results": [
        {"title": "Widget World", "url": "https://w.test/a", "content": "all about widgets"},
        {"title": "Widget FAQ", "url": "https://w.test/b", "content": "faq"},
        {"title": "Extra", "url": "https://w.test/c", "content": "x"},
    ],
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PHANTOM_BROWSER_DB", str(tmp_path / "pb.db"))
    from browser import server

    async def fake_start(): ...
    async def fake_stop(): ...
    monkeypatch.setattr(server.engine, "start", fake_start)
    monkeypatch.setattr(server.engine, "stop", fake_stop)

    class FakeResp:
        status_code = 200
        def json(self):
            return SEARX_JSON
        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *a, **kw): ...
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, url, **kw):
            assert "/search" in url
            return FakeResp()

    monkeypatch.setattr(server.httpx, "AsyncClient", FakeClient)

    async def fake_scrape(url, max_chars=3000, **kw):
        return {"success": True, "markdown": f"content-of {url}"}

    monkeypatch.setattr(server, "do_scrape", fake_scrape)
    with TestClient(server.app) as c:
        yield c


def test_search_maps_results(client):
    r = client.post("/search", json={"query": "widgets", "n": 2})
    body = r.json()
    assert body["success"] is True and body["source"] == "searxng"
    assert len(body["results"]) == 2
    assert body["results"][0] == {
        "title": "Widget World", "url": "https://w.test/a", "snippet": "all about widgets",
    }


def test_search_fetch_content(client):
    r = client.post("/search", json={"query": "widgets", "n": 1, "fetch_content": True})
    body = r.json()
    assert body["results"][0]["content"] == "content-of https://w.test/a"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/browser/test_server_search.py -v`
Expected: FAIL — 404 on `/search` (route missing).

- [ ] **Step 4: Add to `browser/server.py`**

```python
class SearchReq(BaseModel):
    query: str
    n: int = 5
    fetch_content: bool = False
    max_chars: int = 3000


@app.post("/search")
async def search(req: SearchReq):
    searx = os.environ.get("SEARXNG_URL", "http://localhost:8181")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{searx}/search",
                params={"q": req.query, "format": "json"},
                headers={"User-Agent": UA},
            )
            resp.raise_for_status()
            data = resp.json()
        results = [
            {"title": r.get("title", ""), "url": r.get("url", ""),
             "snippet": (r.get("content") or "")[:300]}
            for r in data.get("results", [])[: req.n]
        ]
        if req.fetch_content:
            pages = await asyncio.gather(
                *(do_scrape(r["url"], max_chars=req.max_chars) for r in results),
                return_exceptions=True,
            )
            for r, p in zip(results, pages):
                if isinstance(p, dict) and p.get("success"):
                    r["content"] = p["markdown"]
                else:
                    r["content"] = f"(fetch failed: {p})"
        return {"success": True, "query": req.query, "source": "searxng", "results": results}
    except Exception as e:
        return {"success": False, "query": req.query, "error": f"{type(e).__name__}: {e}"}
```

Note: `fetch_content` must call `do_scrape` via module global lookup (as written) so tests can monkeypatch `server.do_scrape`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/browser/test_server_search.py -v`
Expected: 2 passed.

---

### Task 6: `/map` — URL discovery

**Files:**
- Modify: `browser/server.py` (add `/map`)
- Test: `tests/browser/test_server_map.py`

**Interfaces:**
- Consumes: `do_scrape` (fallback link sweep).
- Produces: `POST /map` `{url, limit=200}` → `{"success", "url", "count", "urls": [str], "source": "sitemap"|"links"}`.

- [ ] **Step 1: Write the failing tests** — `tests/browser/test_server_map.py`

```python
import pytest
from fastapi.testclient import TestClient

SITEMAP = """<?xml version="1.0"?><urlset>
<url><loc>https://m.test/page1</loc></url>
<url><loc> https://m.test/page2 </loc></url>
</urlset>"""


def make_client(tmp_path, monkeypatch, sitemap_status=200, sitemap_body=SITEMAP):
    monkeypatch.setenv("PHANTOM_BROWSER_DB", str(tmp_path / "pb.db"))
    from browser import server

    async def fake_start(): ...
    async def fake_stop(): ...
    monkeypatch.setattr(server.engine, "start", fake_start)
    monkeypatch.setattr(server.engine, "stop", fake_stop)

    class FakeResp:
        def __init__(self, status, text):
            self.status_code = status
            self.text = text

    class FakeClient:
        def __init__(self, *a, **kw): ...
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, url, **kw):
            if url.endswith(("sitemap.xml", "sitemap_index.xml")):
                return FakeResp(sitemap_status, sitemap_body)
            return FakeResp(404, "")

    monkeypatch.setattr(server.httpx, "AsyncClient", FakeClient)

    async def fake_scrape(url, max_chars=1000, **kw):
        return {"success": True, "markdown": "", "links": [
            "https://m.test/x", "https://other.test/y", "https://m.test/z"]}

    monkeypatch.setattr(server, "do_scrape", fake_scrape)
    return TestClient(server.app)


def test_map_uses_sitemap(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as c:
        body = c.post("/map", json={"url": "https://m.test/"}).json()
    assert body["success"] is True and body["source"] == "sitemap"
    assert body["urls"] == ["https://m.test/page1", "https://m.test/page2"]


def test_map_falls_back_to_links_same_domain_only(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch, sitemap_status=404, sitemap_body="") as c:
        body = c.post("/map", json={"url": "https://m.test/"}).json()
    assert body["source"] == "links"
    assert body["urls"] == ["https://m.test/x", "https://m.test/z"]


def test_map_respects_limit(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as c:
        body = c.post("/map", json={"url": "https://m.test/", "limit": 1}).json()
    assert body["count"] == 1 and len(body["urls"]) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/browser/test_server_map.py -v`
Expected: FAIL — 404 on `/map`.

- [ ] **Step 3: Add to `browser/server.py`**

```python
import re as _re
from urllib.parse import urljoin, urlparse


class MapReq(BaseModel):
    url: str
    limit: int = 200


@app.post("/map")
async def map_url(req: MapReq):
    try:
        parsed = urlparse(req.url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        urls: list[str] = []
        seen: set[str] = set()
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            for sm in (urljoin(origin, "/sitemap.xml"), urljoin(origin, "/sitemap_index.xml")):
                if urls:
                    break
                try:
                    resp = await client.get(sm, headers={"User-Agent": UA})
                except httpx.HTTPError:
                    continue
                if resp.status_code != 200 or "<loc>" not in resp.text:
                    continue
                locs = [l.strip() for l in _re.findall(r"<loc>\s*(.*?)\s*</loc>", resp.text)]
                # one level of nested sitemap expansion
                for loc in locs:
                    if len(urls) >= req.limit:
                        break
                    if loc.endswith(".xml"):
                        try:
                            sub = await client.get(loc, headers={"User-Agent": UA})
                            for l2 in _re.findall(r"<loc>\s*(.*?)\s*</loc>", sub.text):
                                l2 = l2.strip()
                                if not l2.endswith(".xml") and l2 not in seen:
                                    seen.add(l2)
                                    urls.append(l2)
                                if len(urls) >= req.limit:
                                    break
                        except httpx.HTTPError:
                            continue
                    elif loc not in seen:
                        seen.add(loc)
                        urls.append(loc)
        source = "sitemap"
        if not urls:
            source = "links"
            page = await do_scrape(req.url, max_chars=1000)
            urls = [u for u in page.get("links", [])
                    if urlparse(u).netloc == parsed.netloc][: req.limit]
        urls = urls[: req.limit]
        return {"success": True, "url": req.url, "count": len(urls),
                "urls": urls, "source": source}
    except Exception as e:
        return {"success": False, "url": req.url, "error": f"{type(e).__name__}: {e}"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/browser/test_server_map.py -v`
Expected: 3 passed.

---

### Task 7: `browser/crawler.py` + `/crawl` routes

**Files:**
- Create: `browser/crawler.py`
- Modify: `browser/server.py` (add `/crawl` POST + GET, startup requeue)
- Test: `tests/browser/test_crawler.py`, `tests/browser/test_server_crawl.py`

**Interfaces:**
- Consumes: `browser.db` job functions, `do_scrape` (injected as `scrape_fn`).
- Produces: `normalize_url(url) -> str`, `should_visit(url, root_url, visited, include_paths=None, exclude_paths=None, same_domain=True) -> bool`, `robots_allows(url, ua="PhantomBrowser") -> bool`, `async run_crawl(job_id, scrape_fn, params)`. Routes: `POST /crawl` `{url, max_pages=50, max_depth=3, max_chars=3000, include_paths?, exclude_paths?, same_domain=True, ignore_robots=False}` → `{"success", "job_id"}`; `GET /crawl/{job_id}?include_content=true` → `{"success", "job": {...}, "pages": [...]}` (markdown omitted when `include_content=false`).

- [ ] **Step 1: Write the failing tests** — `tests/browser/test_crawler.py`

```python
import asyncio

from browser.crawler import normalize_url, should_visit, run_crawl


def test_normalize_url():
    assert normalize_url("https://a.com") == "https://a.com/"
    assert normalize_url("https://a.com/x#frag") == "https://a.com/x"


def test_should_visit_rules():
    root = "https://a.com/"
    assert should_visit("https://a.com/page", root, set())
    assert not should_visit("https://b.com/page", root, set())          # cross-domain
    assert should_visit("https://b.com/page", root, set(), same_domain=False)
    assert not should_visit("https://a.com/page", root, {"https://a.com/page"})  # visited
    assert not should_visit("https://a.com/img.png", root, set())       # binary
    assert not should_visit("ftp://a.com/x", root, set())               # scheme
    assert should_visit("https://a.com/docs/x", root, set(), include_paths=[r"^/docs"])
    assert not should_visit("https://a.com/blog/x", root, set(), include_paths=[r"^/docs"])
    assert not should_visit("https://a.com/admin/x", root, set(), exclude_paths=[r"^/admin"])


def test_run_crawl_bfs_and_caps(tmp_path, monkeypatch):
    monkeypatch.setenv("PHANTOM_BROWSER_DB", str(tmp_path / "pb.db"))
    from browser import db
    db.init()
    site = {
        "https://a.com/": {"title": "root", "links": ["https://a.com/p1", "https://a.com/p2"]},
        "https://a.com/p1": {"title": "p1", "links": ["https://a.com/p3"]},
        "https://a.com/p2": {"title": "p2", "links": []},
        "https://a.com/p3": {"title": "p3", "links": []},
    }

    async def fake_scrape(url, max_chars=3000, **kw):
        entry = site[url]
        return {"success": True, "title": entry["title"], "markdown": f"md {url}",
                "links": entry["links"]}

    params = {"url": "https://a.com", "max_pages": 3, "ignore_robots": True}
    jid = db.create_job("https://a.com", params)
    asyncio.run(run_crawl(jid, fake_scrape, params))
    job = db.get_job(jid)
    assert job["status"] == "done"
    pages = db.job_pages(jid)
    assert len(pages) == 3                       # cap respected
    assert pages[0]["url"] == "https://a.com/"   # BFS: root first


def test_run_crawl_records_page_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("PHANTOM_BROWSER_DB", str(tmp_path / "pb.db"))
    from browser import db
    db.init()

    async def fail_scrape(url, max_chars=3000, **kw):
        raise TimeoutError("nav timeout")

    jid = db.create_job("https://a.com", {})
    asyncio.run(run_crawl(jid, fail_scrape, {"url": "https://a.com", "ignore_robots": True}))
    job = db.get_job(jid)
    assert job["status"] == "done"               # job completes; page marked error
    pages = db.job_pages(jid)
    assert pages[0]["status"] == "error" and "nav timeout" in pages[0]["error"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/browser/test_crawler.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'browser.crawler'`.

- [ ] **Step 3: Write `browser/crawler.py`**

```python
"""BFS crawl: pure frontier logic + the async job runner. robots.txt is
honored for bulk crawls only (spec: single-page scrape/sessions are 'browsing
like Serge would' and skip it)."""
import re
import urllib.robotparser
from urllib.parse import urldefrag, urlparse

try:
    from browser import db
except ImportError:  # pragma: no cover
    import db

BINARY_RX = re.compile(
    r"\.(png|jpe?g|gif|webp|svg|ico|css|js|mjs|pdf|zip|gz|tar|mp4|mp3|wav|woff2?|ttf|eot)($|\?)",
    re.I,
)


def normalize_url(url: str) -> str:
    url, _ = urldefrag(url)
    if urlparse(url).path == "":
        url += "/"
    return url


def should_visit(url, root_url, visited, include_paths=None, exclude_paths=None,
                 same_domain=True) -> bool:
    if not url.startswith(("http://", "https://")):
        return False
    if url in visited:
        return False
    p, r = urlparse(url), urlparse(root_url)
    if same_domain and p.netloc != r.netloc:
        return False
    path = p.path or "/"
    if BINARY_RX.search(path):
        return False
    if include_paths and not any(re.search(pat, path) for pat in include_paths):
        return False
    if exclude_paths and any(re.search(pat, path) for pat in exclude_paths):
        return False
    return True


_robots: dict[str, object] = {}


def robots_allows(url: str, ua: str = "PhantomBrowser") -> bool:
    p = urlparse(url)
    origin = f"{p.scheme}://{p.netloc}"
    rp = _robots.get(origin)
    if rp is None:
        parser = urllib.robotparser.RobotFileParser(origin + "/robots.txt")
        try:
            parser.read()
            rp = parser
        except Exception:
            rp = "unreachable"  # no robots.txt reachable → allow
        _robots[origin] = rp
    if rp == "unreachable":
        return True
    return rp.can_fetch(ua, url)


async def run_crawl(job_id: int, scrape_fn, params: dict) -> None:
    """scrape_fn: async (url, max_chars=...) -> scrape dict (do_scrape)."""
    root = normalize_url(params["url"])
    max_pages = int(params.get("max_pages", 50))
    max_depth = int(params.get("max_depth", 3))
    max_chars = int(params.get("max_chars", 3000))
    include_paths = params.get("include_paths")
    exclude_paths = params.get("exclude_paths")
    same_domain = bool(params.get("same_domain", True))
    ignore_robots = bool(params.get("ignore_robots", False))

    db.set_job_status(job_id, "running")
    queue: list[tuple[str, int]] = [(root, 0)]
    visited: set[str] = set()
    try:
        while queue and len(visited) < max_pages:
            url, depth = queue.pop(0)
            url = normalize_url(url)
            if url in visited:
                continue
            visited.add(url)
            if not ignore_robots and not robots_allows(url):
                db.add_page(job_id, url, None, None, status="error",
                            error="robots.txt disallow")
                continue
            try:
                page = await scrape_fn(url, max_chars=max_chars)
            except Exception as e:
                db.add_page(job_id, url, None, None, status="error",
                            error=f"{type(e).__name__}: {e}")
                continue
            if not page.get("success"):
                db.add_page(job_id, url, None, None, status="error",
                            error=page.get("error", "scrape failed"))
                continue
            db.add_page(job_id, url, page.get("title"), page.get("markdown"))
            if depth < max_depth:
                queued = {q for q, _ in queue}
                for link in page.get("links", []):
                    ln = normalize_url(link)
                    if ln not in queued and should_visit(
                        ln, root, visited, include_paths, exclude_paths, same_domain
                    ):
                        queue.append((ln, depth + 1))
                        queued.add(ln)
        db.set_job_status(job_id, "done")
    except Exception as e:
        db.set_job_status(job_id, "error", error=f"{type(e).__name__}: {e}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/browser/test_crawler.py -v`
Expected: 4 passed.

- [ ] **Step 5: Write the failing route tests** — `tests/browser/test_server_crawl.py`

```python
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PHANTOM_BROWSER_DB", str(tmp_path / "pb.db"))
    from browser import server

    async def fake_start(): ...
    async def fake_stop(): ...
    monkeypatch.setattr(server.engine, "start", fake_start)
    monkeypatch.setattr(server.engine, "stop", fake_stop)

    async def fake_scrape(url, max_chars=3000, **kw):
        return {"success": True, "title": "T", "markdown": f"md {url}", "links": []}

    monkeypatch.setattr(server, "do_scrape", fake_scrape)
    with TestClient(server.app) as c:
        yield c


def test_crawl_job_lifecycle(client):
    r = client.post("/crawl", json={"url": "https://c.test/", "max_pages": 2,
                                    "ignore_robots": True})
    jid = r.json()["job_id"]
    assert r.json()["success"] is True
    for _ in range(50):  # poll until background task finishes
        body = client.get(f"/crawl/{jid}").json()
        if body["job"]["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert body["job"]["status"] == "done"
    assert body["pages"][0]["markdown"] == "md https://c.test/"


def test_crawl_get_without_content(client):
    r = client.post("/crawl", json={"url": "https://c.test/", "max_pages": 1,
                                    "ignore_robots": True})
    jid = r.json()["job_id"]
    for _ in range(50):
        body = client.get(f"/crawl/{jid}?include_content=false").json()
        if body["job"]["status"] == "done":
            break
        time.sleep(0.1)
    assert "markdown" not in body["pages"][0]


def test_crawl_unknown_job(client):
    assert client.get("/crawl/99999").json()["success"] is False
```

- [ ] **Step 6: Run to verify they fail**

Run: `venv/bin/python -m pytest tests/browser/test_server_crawl.py -v`
Expected: FAIL — 404 on `/crawl`.

- [ ] **Step 7: Add to `browser/server.py`**

Import at top (with the existing try/except pair):

```python
try:
    from browser import crawler
except ImportError:  # pragma: no cover
    import crawler
```

Routes + startup requeue:

```python
class CrawlReq(BaseModel):
    url: str
    max_pages: int = 50
    max_depth: int = 3
    max_chars: int = 3000
    include_paths: list[str] | None = None
    exclude_paths: list[str] | None = None
    same_domain: bool = True
    ignore_robots: bool = False


def _launch_crawl(job_id: int, params: dict) -> None:
    async def scrape_fn(url, max_chars=3000, **kw):
        return await do_scrape(url, max_chars=max_chars)
    asyncio.create_task(crawler.run_crawl(job_id, scrape_fn, params))


@app.post("/crawl")
async def crawl_start(req: CrawlReq):
    params = req.model_dump()
    job_id = db.create_job(req.url, params)
    _launch_crawl(job_id, params)
    return {"success": True, "job_id": job_id}


@app.get("/crawl/{job_id}")
async def crawl_status(job_id: int, include_content: bool = True):
    job = db.get_job(job_id)
    if not job:
        return {"success": False, "error": f"no such job {job_id}"}
    pages = db.job_pages(job_id)
    if not include_content:
        pages = [{k: v for k, v in p.items() if k != "markdown"} for p in pages]
    return {"success": True, "job": job, "pages": pages}
```

And inside `lifespan`, after `await engine.start()`:

```python
    import json as _json
    for jid in db.requeue_running():
        job = db.get_job(jid)
        log.info("requeueing crawl job %s after restart", jid)
        _launch_crawl(jid, _json.loads(job["params"]))
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/browser/test_server_crawl.py tests/browser/test_crawler.py -v`
Expected: 7 passed.

---

### Task 8: `browser/extractor.py` + `/extract`

**Files:**
- Create: `browser/extractor.py`
- Modify: `browser/server.py` (add `/extract`)
- Test: `tests/browser/test_extractor.py`, `tests/browser/test_server_extract.py`

**Interfaces:**
- Consumes: `do_scrape` (when given url/urls).
- Produces: `validate(data, schema) -> list[str]` (error strings, empty = valid; supports `type`, `required`, `properties`, array `items` — the subset agents need); `async extract(content, schema, prompt=None, model=None) -> {"success", "data"|"error", "model"}`. Route `POST /extract` `{schema, url? | urls? | content?, prompt?, model?}` → extract result + `"sources": [urls]`. Ollama call: `POST {OLLAMA_URL}/api/chat` with `format: <schema>`, `stream: False`, `options: {num_ctx: 16384, temperature: 0}`, one retry on validation failure.

- [ ] **Step 1: Write the failing tests** — `tests/browser/test_extractor.py`

```python
import asyncio
import json

import pytest

from browser.extractor import extract, validate

SCHEMA = {
    "type": "object",
    "required": ["vendor", "total"],
    "properties": {
        "vendor": {"type": "string"},
        "total": {"type": "number"},
        "items": {"type": "array", "items": {"type": "string"}},
    },
}


def test_validate_ok():
    assert validate({"vendor": "HD", "total": 9.5, "items": ["a"]}, SCHEMA) == []


def test_validate_catches_missing_and_wrong_type():
    errs = validate({"total": "nine"}, SCHEMA)
    assert any("vendor" in e for e in errs)
    assert any("total" in e for e in errs)


def _fake_ollama(monkeypatch, replies):
    """replies: list of message-content strings returned in order."""
    from browser import extractor
    calls = {"n": 0}

    class FakeResp:
        def __init__(self, content):
            self._c = content
        def raise_for_status(self):
            return None
        def json(self):
            return {"message": {"content": self._c}}

    class FakeClient:
        def __init__(self, *a, **kw): ...
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, **kw):
            assert url.endswith("/api/chat")
            content = replies[min(calls["n"], len(replies) - 1)]
            calls["n"] += 1
            return FakeResp(content)

    monkeypatch.setattr(extractor.httpx, "AsyncClient", FakeClient)
    return calls


def test_extract_success_first_try(monkeypatch):
    _fake_ollama(monkeypatch, [json.dumps({"vendor": "HD", "total": 9.5})])
    out = asyncio.run(extract("page text", SCHEMA))
    assert out["success"] is True and out["data"]["vendor"] == "HD"


def test_extract_retries_then_succeeds(monkeypatch):
    calls = _fake_ollama(monkeypatch, [
        json.dumps({"total": 1}),                       # missing vendor → retry
        json.dumps({"vendor": "HD", "total": 1}),
    ])
    out = asyncio.run(extract("page text", SCHEMA))
    assert out["success"] is True and calls["n"] == 2


def test_extract_fails_after_retry(monkeypatch):
    _fake_ollama(monkeypatch, ["not json at all"])
    out = asyncio.run(extract("page text", SCHEMA))
    assert out["success"] is False and "invalid JSON" in out["error"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/browser/test_extractor.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Write `browser/extractor.py`**

```python
"""Schema-guided extraction: page markdown → local Ollama → validated JSON.
LOCAL ONLY (house hard rule) — OLLAMA_URL defaults to the AMD instance."""
import json
import os

import httpx

_TYPES = {
    "string": str, "number": (int, float), "integer": int,
    "boolean": bool, "array": list, "object": dict,
}


def validate(data, schema, path="$") -> list[str]:
    """Minimal JSON-schema subset: type / required / properties / items."""
    errs: list[str] = []
    t = schema.get("type")
    if t in _TYPES and not isinstance(data, _TYPES[t]):
        if not (t == "number" and isinstance(data, int)):
            return [f"{path}: expected {t}, got {type(data).__name__}"]
    if t == "object" and isinstance(data, dict):
        for key in schema.get("required", []):
            if key not in data or data[key] is None:
                errs.append(f"{path}.{key}: required field missing")
        for key, sub in (schema.get("properties") or {}).items():
            if key in data and data[key] is not None:
                errs.extend(validate(data[key], sub, f"{path}.{key}"))
    if t == "array" and isinstance(data, list) and schema.get("items"):
        for i, item in enumerate(data):
            errs.extend(validate(item, schema["items"], f"{path}[{i}]"))
    return errs


async def extract(content: str, schema: dict, prompt: str | None = None,
                  model: str | None = None) -> dict:
    ollama = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    model = model or os.environ.get("PB_EXTRACT_MODEL", "glm-4.7-flash")
    system = (
        "Extract structured data from the provided web page content. "
        "Respond with JSON matching the requested schema exactly. "
        "Use null for values not present in the content — never invent data."
    )
    user = (
        f"{prompt or 'Extract the data described by the schema.'}\n\n"
        f"PAGE CONTENT:\n{content[:24000]}"
    )
    last_err = None
    for _ in range(2):
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        if last_err:
            messages.append({
                "role": "user",
                "content": f"Previous attempt failed validation: {last_err}. "
                           "Return corrected JSON only.",
            })
        async with httpx.AsyncClient(timeout=180) as client:
            resp = await client.post(f"{ollama}/api/chat", json={
                "model": model, "messages": messages, "format": schema,
                "stream": False, "options": {"num_ctx": 16384, "temperature": 0},
            })
            resp.raise_for_status()
            raw = resp.json()["message"]["content"]
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            last_err = f"invalid JSON: {e}"
            continue
        errs = validate(data, schema)
        if not errs:
            return {"success": True, "data": data, "model": model}
        last_err = "; ".join(errs[:5])
    return {"success": False, "error": f"validation failed after retry: {last_err}",
            "model": model}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/browser/test_extractor.py -v`
Expected: 5 passed.

- [ ] **Step 5: Write the failing route test** — `tests/browser/test_server_extract.py`

```python
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PHANTOM_BROWSER_DB", str(tmp_path / "pb.db"))
    from browser import server

    async def fake_start(): ...
    async def fake_stop(): ...
    monkeypatch.setattr(server.engine, "start", fake_start)
    monkeypatch.setattr(server.engine, "stop", fake_stop)

    async def fake_scrape(url, max_chars=8000, **kw):
        return {"success": True, "markdown": f"# Page {url}\nvendor: HD total: 9.5"}

    async def fake_extract(content, schema, prompt=None, model=None):
        assert "vendor: HD" in content
        return {"success": True, "data": {"vendor": "HD"}, "model": "fake"}

    monkeypatch.setattr(server, "do_scrape", fake_scrape)
    monkeypatch.setattr(server.extractor, "extract", fake_extract)
    with TestClient(server.app) as c:
        yield c


def test_extract_from_url(client):
    r = client.post("/extract", json={
        "url": "https://e.test/p",
        "schema": {"type": "object", "required": ["vendor"],
                   "properties": {"vendor": {"type": "string"}}},
    })
    body = r.json()
    assert body["success"] is True
    assert body["data"] == {"vendor": "HD"}
    assert body["sources"] == ["https://e.test/p"]


def test_extract_requires_input(client):
    r = client.post("/extract", json={"schema": {"type": "object"}})
    assert r.json()["success"] is False
```

- [ ] **Step 6: Run to verify it fails**

Run: `venv/bin/python -m pytest tests/browser/test_server_extract.py -v`
Expected: FAIL — 404 on `/extract`.

- [ ] **Step 7: Add to `browser/server.py`**

Import (in the existing try/except):

```python
try:
    from browser import extractor
except ImportError:  # pragma: no cover
    import extractor
```

Route (note: pydantic field can't be named `schema` on the model attribute without alias trouble — use `Field(alias=...)`-free approach with `json_schema` attr + alias):

```python
from pydantic import Field


class ExtractReq(BaseModel):
    json_schema: dict = Field(alias="schema")
    url: str | None = None
    urls: list[str] | None = None
    content: str | None = None
    prompt: str | None = None
    model: str | None = None

    model_config = {"populate_by_name": True}


@app.post("/extract")
async def extract_route(req: ExtractReq):
    try:
        sources: list[str] = []
        content = req.content or ""
        urls = req.urls or ([req.url] if req.url else [])
        for u in urls[:5]:
            page = await do_scrape(u, max_chars=8000)
            if page.get("success"):
                sources.append(u)
                content += f"\n\n=== {u} ===\n{page['markdown']}"
        if not content.strip():
            return {"success": False, "error": "no content: pass url, urls or content"}
        out = await extractor.extract(content, req.json_schema, req.prompt, req.model)
        out["sources"] = sources
        return out
    except Exception as e:
        return {"success": False, "error": f"{type(e).__name__}: {e}"}
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/browser/test_server_extract.py -v`
Expected: 2 passed.

---

### Task 9: `browser/sessions.py` + session routes

**Files:**
- Create: `browser/sessions.py`
- Modify: `browser/server.py` (session routes + reaper in lifespan)
- Test: `tests/browser/test_sessions.py` (integration — real Chromium, data:/local HTML only)

**Interfaces:**
- Consumes: `Engine.new_context`, `page_to_md`.
- Produces: `class SessionManager(engine, idle_ttl=600, max_sessions=8)` — `async create(profile=None) -> str` (session id), `get(sid) -> Session` (raises `KeyError`), `async close(sid)`, `async close_all()`, `async reap_once() -> int`, `async read(sid, max_chars=6000) -> dict`, `async act(sid, op, **kw) -> dict`, `async element_info(sid, index) -> dict|None`, `async active_element(sid) -> dict|None`.
- `read` returns `{"success", "url", "title", "markdown", "elements": [{"idx","tag","type","text","in_form","form_method"}]}` — elements tagged in-DOM with `data-pb-idx`.
- `act` ops: `goto(url=)`, `click(index=)`, `type(index=, text=)`, `press(key=)`, `scroll(dy=)`, `back()`, `screenshot()`. Returns `{"success": True, "url": <current>}` (+`screenshot_path` for screenshot) or `{"success": False, "error", "hint"}`.
- Routes (all under `/session`): `POST /session {profile?}` → `{"success", "session_id", "profile"}`; `POST /session/{sid}/goto|read|click|type|press|scroll|back|screenshot`; `DELETE /session/{sid}`. Unknown sid → `{"success": False, "error": "unknown or expired session", "hint": "create a new session"}`. Gate hooks are added in Task 10 — this task wires sessions ungated.

- [ ] **Step 1: Write the failing tests** — `tests/browser/test_sessions.py`

```python
import asyncio

import pytest

from browser.engine import Engine
from browser.sessions import SessionManager

# NOTE: Playwright objects are event-loop-bound, so every test runs its FULL
# engine lifecycle inside ONE asyncio.run(). Chromium also blocks click-nav to
# data: URLs, so pages live in temp files reached via file://.

PAGE2_HTML = "<html><head><title>Second</title></head><body><h1>second page</h1></body></html>"


def page1_html(page2_url: str) -> str:
    return (
        "<html><head><title>SessTest</title></head><body>"
        "<h1>Session page</h1>"
        "<form method='post' action='/go'>"
        "<input type='text' id='q' placeholder='Search box'>"
        "<button type='submit'>Send it</button></form>"
        f"<a href='{page2_url}'>Next page</a>"
        "</body></html>"
    )


def make_pages(tmp_path) -> str:
    p2 = tmp_path / "page2.html"
    p2.write_text(PAGE2_HTML)
    p1 = tmp_path / "page1.html"
    p1.write_text(page1_html(p2.as_uri()))
    return p1.as_uri()


async def with_mgr(coro_fn):
    eng = Engine()
    await eng.start()
    mgr = SessionManager(eng)
    try:
        return await coro_fn(mgr)
    finally:
        await mgr.close_all()
        await eng.stop()


@pytest.mark.integration
def test_session_goto_read_elements(tmp_path):
    url = make_pages(tmp_path)

    async def go(mgr):
        sid = await mgr.create()
        await mgr.act(sid, "goto", url=url)
        return await mgr.read(sid)

    out = asyncio.run(with_mgr(go))
    assert out["success"] is True
    assert "Session page" in out["markdown"]
    texts = [e["text"] for e in out["elements"]]
    assert any("Send it" in t for t in texts)
    assert any("Next page" in t for t in texts)
    send = next(e for e in out["elements"] if "Send it" in e["text"])
    assert send["in_form"] is True and send["form_method"] == "post"


@pytest.mark.integration
def test_type_then_click_link_navigates(tmp_path):
    url = make_pages(tmp_path)

    async def go(mgr):
        sid = await mgr.create()
        await mgr.act(sid, "goto", url=url)
        read1 = await mgr.read(sid)
        box = next(e for e in read1["elements"] if e["tag"] == "input")
        await mgr.act(sid, "type", index=box["idx"], text="hello")
        link = next(e for e in read1["elements"] if "Next page" in e["text"])
        await mgr.act(sid, "click", index=link["idx"])
        return await mgr.read(sid)

    out = asyncio.run(with_mgr(go))
    assert "second page" in out["markdown"]


@pytest.mark.integration
def test_stale_index_gives_hint(tmp_path):
    url = make_pages(tmp_path)

    async def go(mgr):
        sid = await mgr.create()
        await mgr.act(sid, "goto", url=url)
        # click without read → no data-pb-idx attributes tagged yet
        return await mgr.act(sid, "click", index=99)

    out = asyncio.run(with_mgr(go))
    assert out["success"] is False and "read" in out["hint"]


@pytest.mark.integration
def test_reaper_closes_idle(tmp_path):
    async def go(mgr):
        sid = await mgr.create()
        mgr.get(sid).last_used -= 9999
        n = await mgr.reap_once()
        try:
            mgr.get(sid)
            alive = True
        except KeyError:
            alive = False
        return n, alive

    n, alive = asyncio.run(with_mgr(go))
    assert n == 1 and alive is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `venv/bin/python -m pytest tests/browser/test_sessions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'browser.sessions'`.

- [ ] **Step 3: Write `browser/sessions.py`**

```python
"""Stateful interactive browser sessions. The agent reads the page as markdown
plus a NUMBERED list of interactive elements (tagged in-DOM with data-pb-idx),
then acts by index. Sessions idle out after idle_ttl seconds."""
import asyncio
import time
import uuid
from pathlib import Path

from playwright.async_api import Error as PWError

try:
    from browser.engine import SCREENSHOT_DIR
    from browser.page_to_md import page_to_md
except ImportError:  # pragma: no cover
    from engine import SCREENSHOT_DIR
    from page_to_md import page_to_md

READ_JS = """() => {
  document.querySelectorAll('[data-pb-idx]').forEach(el => el.removeAttribute('data-pb-idx'));
  const els = Array.from(document.querySelectorAll(
    'a[href], button, input, select, textarea, [role="button"], [onclick]'
  )).filter(el => {
    const st = window.getComputedStyle(el);
    return st.display !== 'none' && st.visibility !== 'hidden';
  }).slice(0, 150);
  return els.map((el, i) => {
    el.setAttribute('data-pb-idx', String(i));
    const f = el.closest('form');
    const label = (el.innerText || el.value || el.placeholder ||
                   el.getAttribute('aria-label') || el.getAttribute('title') || ''
                  ).trim().replace(/\\s+/g, ' ').slice(0, 80);
    return {idx: i, tag: el.tagName.toLowerCase(),
            type: el.getAttribute('type') || '',
            text: label, in_form: !!f,
            form_method: f ? (f.method || 'get').toLowerCase() : ''};
  });
}"""

ELEMENT_INFO_JS = """(idx) => {
  const el = document.querySelector('[data-pb-idx="' + idx + '"]');
  if (!el) return null;
  const f = el.closest('form');
  return {tag: el.tagName.toLowerCase(), type: el.getAttribute('type') || '',
          text: (el.innerText || el.value || '').trim().slice(0, 80),
          in_form: !!f, form_method: f ? (f.method || 'get').toLowerCase() : ''};
}"""

ACTIVE_ELEMENT_JS = """() => {
  const el = document.activeElement;
  if (!el || el === document.body) return null;
  const f = el.closest('form');
  return {tag: el.tagName.toLowerCase(), type: el.getAttribute('type') || '',
          text: (el.innerText || el.value || '').trim().slice(0, 80),
          in_form: !!f, form_method: f ? (f.method || 'get').toLowerCase() : ''};
}"""


class Session:
    def __init__(self, sid: str, context, page, profile: str | None):
        self.id = sid
        self.context = context
        self.page = page
        self.profile = profile
        self.last_used = time.monotonic()

    def touch(self):
        self.last_used = time.monotonic()


class SessionManager:
    def __init__(self, engine, idle_ttl: int = 600, max_sessions: int = 8):
        self.engine = engine
        self.idle_ttl = idle_ttl
        self.max_sessions = max_sessions
        self._sessions: dict[str, Session] = {}

    async def create(self, profile: str | None = None) -> str:
        if len(self._sessions) >= self.max_sessions:
            raise RuntimeError(f"max {self.max_sessions} sessions; close one first")
        ctx = await self.engine.new_context(profile=profile)
        page = ctx.pages[0] if getattr(ctx, "pages", None) else await ctx.new_page()
        sid = uuid.uuid4().hex[:12]
        self._sessions[sid] = Session(sid, ctx, page, profile)
        return sid

    def get(self, sid: str) -> Session:
        s = self._sessions.get(sid)
        if s is None:
            raise KeyError(sid)
        s.touch()
        return s

    async def close(self, sid: str) -> None:
        s = self._sessions.pop(sid, None)
        if s:
            try:
                await s.context.close()
            except Exception:
                pass

    async def close_all(self) -> None:
        for sid in list(self._sessions):
            await self.close(sid)

    async def reap_once(self) -> int:
        now = time.monotonic()
        stale = [sid for sid, s in self._sessions.items()
                 if now - s.last_used > self.idle_ttl]
        for sid in stale:
            await self.close(sid)
        return len(stale)

    async def element_info(self, sid: str, index: int):
        s = self.get(sid)
        return await s.page.evaluate(ELEMENT_INFO_JS, int(index))

    async def active_element(self, sid: str):
        s = self.get(sid)
        return await s.page.evaluate(ACTIVE_ELEMENT_JS)

    async def read(self, sid: str, max_chars: int = 6000) -> dict:
        s = self.get(sid)
        elements = await s.page.evaluate(READ_JS)
        html = await s.page.content()
        md = page_to_md(html, s.page.url, max_chars=max_chars)
        return {"success": True, "url": s.page.url, "title": md["title"],
                "markdown": md["markdown"], "elements": elements}

    async def act(self, sid: str, op: str, **kw) -> dict:
        s = self.get(sid)
        page = s.page
        try:
            if op == "goto":
                await page.goto(kw["url"], wait_until="domcontentloaded", timeout=30000)
            elif op == "click":
                loc = page.locator(f'[data-pb-idx="{int(kw["index"])}"]')
                if await loc.count() == 0:
                    return {"success": False,
                            "error": f'no element with index {kw["index"]}',
                            "hint": "call read to refresh element indexes"}
                await loc.click(timeout=10000)
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=8000)
                except PWError:
                    pass
            elif op == "type":
                loc = page.locator(f'[data-pb-idx="{int(kw["index"])}"]')
                if await loc.count() == 0:
                    return {"success": False,
                            "error": f'no element with index {kw["index"]}',
                            "hint": "call read to refresh element indexes"}
                await loc.fill(kw.get("text", ""), timeout=10000)
                await loc.focus()
            elif op == "press":
                await page.keyboard.press(kw.get("key", "Enter"))
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=8000)
                except PWError:
                    pass
            elif op == "scroll":
                await page.mouse.wheel(0, int(kw.get("dy", 800)))
            elif op == "back":
                await page.go_back(wait_until="domcontentloaded", timeout=15000)
            elif op == "screenshot":
                SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
                path = str(SCREENSHOT_DIR / f"session_{sid}_{int(time.time()*1000)}.png")
                await page.screenshot(path=path, full_page=False)
                return {"success": True, "url": page.url, "screenshot_path": path}
            else:
                return {"success": False, "error": f"unknown op '{op}'",
                        "hint": "ops: goto/click/type/press/scroll/back/screenshot"}
            return {"success": True, "url": page.url}
        except PWError as e:
            return {"success": False, "error": f"playwright: {str(e).splitlines()[0]}",
                    "hint": "call read to see current page state"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/browser/test_sessions.py -v`
Expected: 4 passed (integration — real Chromium, no network).

- [ ] **Step 5: Wire session routes into `browser/server.py`**

Import + manager (top of file, after `engine = Engine()`):

```python
try:
    from browser.sessions import SessionManager
except ImportError:  # pragma: no cover
    from sessions import SessionManager

sessions = SessionManager(engine)
```

In `lifespan`, start the reaper after the crawl requeue and cancel it on shutdown:

```python
    async def _reaper():
        while True:
            await asyncio.sleep(60)
            try:
                n = await sessions.reap_once()
                if n:
                    log.info("reaped %d idle sessions", n)
                db.expire_stale(300)
            except Exception:
                log.exception("reaper iteration failed")

    reaper_task = asyncio.create_task(_reaper())
    yield
    reaper_task.cancel()
    await sessions.close_all()
    await engine.stop()
```

(The existing `yield` / `await engine.stop()` lines are replaced by this block.)

Routes:

```python
class SessionCreateReq(BaseModel):
    profile: str | None = None


class ActReq(BaseModel):
    url: str | None = None
    index: int | None = None
    text: str | None = None
    key: str | None = None
    dy: int | None = None
    max_chars: int = 6000
    approval_id: int | None = None


def _no_session(sid):
    return {"success": False, "error": f"unknown or expired session '{sid}'",
            "hint": "create a new session"}


@app.post("/session")
async def session_create(req: SessionCreateReq):
    try:
        sid = await sessions.create(profile=req.profile)
        return {"success": True, "session_id": sid, "profile": req.profile}
    except (ValueError, RuntimeError) as e:
        return {"success": False, "error": str(e)}


@app.delete("/session/{sid}")
async def session_close(sid: str):
    await sessions.close(sid)
    return {"success": True}


@app.post("/session/{sid}/goto")
async def session_goto(sid: str, req: ActReq):
    try:
        return await sessions.act(sid, "goto", url=req.url)
    except KeyError:
        return _no_session(sid)


@app.post("/session/{sid}/read")
async def session_read(sid: str, req: ActReq):
    try:
        return await sessions.read(sid, max_chars=req.max_chars)
    except KeyError:
        return _no_session(sid)


@app.post("/session/{sid}/click")
async def session_click(sid: str, req: ActReq):
    try:
        return await sessions.act(sid, "click", index=req.index)
    except KeyError:
        return _no_session(sid)


@app.post("/session/{sid}/type")
async def session_type(sid: str, req: ActReq):
    try:
        return await sessions.act(sid, "type", index=req.index, text=req.text or "")
    except KeyError:
        return _no_session(sid)


@app.post("/session/{sid}/press")
async def session_press(sid: str, req: ActReq):
    try:
        return await sessions.act(sid, "press", key=req.key or "Enter")
    except KeyError:
        return _no_session(sid)


@app.post("/session/{sid}/scroll")
async def session_scroll(sid: str, req: ActReq):
    try:
        return await sessions.act(sid, "scroll", dy=req.dy or 800)
    except KeyError:
        return _no_session(sid)


@app.post("/session/{sid}/back")
async def session_back(sid: str, req: ActReq):
    try:
        return await sessions.act(sid, "back")
    except KeyError:
        return _no_session(sid)


@app.post("/session/{sid}/screenshot")
async def session_screenshot(sid: str, req: ActReq):
    try:
        return await sessions.act(sid, "screenshot")
    except KeyError:
        return _no_session(sid)
```

- [ ] **Step 6: Verify the whole suite still passes**

Run: `venv/bin/python -m pytest tests/browser/ -v`
Expected: all pass (existing server tests unaffected — their lifespan monkeypatches leave the reaper harmless).

---

### Task 10: `browser/gate.py` — write gate + approval routes

**Files:**
- Create: `browser/gate.py`
- Modify: `browser/server.py` (gate checks in click/press routes, approval routes)
- Test: `tests/browser/test_gate.py`, `tests/browser/test_server_gate.py`

**Interfaces:**
- Consumes: `db.create_approval/get_approval/decide_approval`, `core.telegram_fmt.post_html`, `sessions.element_info/active_element/act`.
- Produces: `is_gated_click(el: dict|None) -> bool`, `is_gated_press(key: str, active: dict|None) -> bool`, `request_approval(session_id, action: dict, description: str) -> {"success": True, "status": "pending_approval", "approval_id", "detail"}`, `_send_telegram(msg) -> bool` (module-level for test monkeypatching).
- Routes: `GET /approvals/{aid}` → `{"success", "status"}`; `GET /approvals/{aid}/decide?tok=&d=approve|deny` → HTML confirmation; on approve, the queued action executes in the still-open session and status becomes `executed` (or `error` if the session died).
- Gate policy (spec): **profile sessions only**. Click gated when element `type == "submit"`, or (`in_form` and tag in `button`/`input`), or text matches the gated-verb regex. Press gated only for Enter when the focused element is in a `method="post"` form. Unknown element info in a profile session → gated (safe default).

- [ ] **Step 1: Write the failing heuristic tests** — `tests/browser/test_gate.py`

```python
from browser.gate import is_gated_click, is_gated_press


def test_submit_button_gated():
    assert is_gated_click({"tag": "button", "type": "submit", "text": "Go",
                           "in_form": True, "form_method": "post"})


def test_texty_verbs_gated():
    for text in ("Send message", "Buy now", "Delete account", "Confirm order",
                 "Publish post", "Pay $50"):
        assert is_gated_click({"tag": "a", "type": "", "text": text,
                               "in_form": False, "form_method": ""}), text


def test_plain_link_not_gated():
    assert not is_gated_click({"tag": "a", "type": "", "text": "Next page",
                               "in_form": False, "form_method": ""})
    assert not is_gated_click({"tag": "a", "type": "", "text": "Documentation",
                               "in_form": False, "form_method": ""})


def test_unknown_element_gated():
    assert is_gated_click(None)


def test_press_enter_in_post_form_gated():
    active = {"tag": "input", "type": "text", "text": "", "in_form": True,
              "form_method": "post"}
    assert is_gated_press("Enter", active)


def test_press_enter_in_get_form_free():
    active = {"tag": "input", "type": "text", "text": "", "in_form": True,
              "form_method": "get"}
    assert not is_gated_press("Enter", active)      # search boxes stay free
    assert not is_gated_press("Enter", None)
    assert not is_gated_press("Tab", active)
```

- [ ] **Step 2: Run to verify they fail**

Run: `venv/bin/python -m pytest tests/browser/test_gate.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Write `browser/gate.py`**

```python
"""Write gate for logged-in (profile) sessions: state-changing actions pause
for Serge's Telegram approval. Enforced server-side — never by prompt.
Silence (300 s) = denied, per the Specter confirm-before-act rule."""
import logging
import os
import re
import secrets
import sys
from pathlib import Path

try:
    from browser import db
except ImportError:  # pragma: no cover
    import db

# core.telegram_fmt needs the framework root importable
_FW = str(Path(__file__).resolve().parent.parent)
if _FW not in sys.path:
    sys.path.insert(0, _FW)

log = logging.getLogger("phantom_browser.gate")

GATED_RX = re.compile(
    r"\b(submit|send|post|buy|pay|order|delete|remove|confirm|publish|checkout"
    r"|subscribe|apply|book|transfer|purchase|tweet|reply|comment|share|upload"
    r"|save|update|sign)\b",
    re.I,
)


def is_gated_click(el: dict | None) -> bool:
    if not el:
        return True  # can't classify it in a logged-in session → gate it
    if (el.get("type") or "").lower() == "submit":
        return True
    if el.get("in_form") and el.get("tag") in ("button", "input"):
        return True
    return bool(GATED_RX.search(el.get("text") or ""))


def is_gated_press(key: str, active: dict | None) -> bool:
    if key.lower() not in ("enter", "return"):
        return False
    if not active:
        return False
    return bool(active.get("in_form")) and (active.get("form_method") or "") == "post"


def _send_telegram(msg: str) -> bool:
    token = os.environ.get("TELEGRAM_SIMON_BATELY", "")
    chat_id = os.environ.get("SERGE_CHAT_ID", "8551331144")
    if not token:
        log.warning("no TELEGRAM_SIMON_BATELY token — approval message not sent")
        return False
    try:
        from core.telegram_fmt import post_html
        return bool(post_html(token, chat_id, msg))
    except Exception:
        log.exception("telegram send failed")
        return False


def request_approval(session_id: str, action: dict, description: str) -> dict:
    token = secrets.token_urlsafe(16)
    aid = db.create_approval(session_id, action, description, token)
    base = os.environ.get("PB_PUBLIC_URL", "http://100.127.118.103:8100")
    approve = f"{base}/approvals/{aid}/decide?tok={token}&d=approve"
    deny = f"{base}/approvals/{aid}/decide?tok={token}&d=deny"
    _send_telegram(
        f"🔒 **Phantom Browser write gate**\n{description}\n\n"
        f"✅ Approve: {approve}\n❌ Deny: {deny}\n\n"
        f"_No answer in 5 min = denied._"
    )
    return {
        "success": True, "status": "pending_approval", "approval_id": aid,
        "detail": "state-changing action in a logged-in session — Serge pinged on "
                  "Telegram; poll GET /approvals/{id} or the browse skill's "
                  "approval_status action. 5 min silence = denied.",
    }
```

- [ ] **Step 4: Run heuristic tests to verify they pass**

Run: `venv/bin/python -m pytest tests/browser/test_gate.py -v`
Expected: 6 passed.

- [ ] **Step 5: Write failing route tests** — `tests/browser/test_server_gate.py`

```python
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PHANTOM_BROWSER_DB", str(tmp_path / "pb.db"))
    from browser import server, gate

    async def fake_start(): ...
    async def fake_stop(): ...
    monkeypatch.setattr(server.engine, "start", fake_start)
    monkeypatch.setattr(server.engine, "stop", fake_stop)
    monkeypatch.setattr(gate, "_send_telegram", lambda msg: True)

    # fake session layer: one profile session 'psess', one anon 'asess'
    class FakeSessions:
        def __init__(self):
            self.executed = []
        def get(self, sid):
            class S:  # minimal stand-in
                profile = "gmail" if sid == "psess" else None
            if sid not in ("psess", "asess"):
                raise KeyError(sid)
            return S()
        async def element_info(self, sid, index):
            return {"tag": "button", "type": "submit", "text": "Send it",
                    "in_form": True, "form_method": "post"}
        async def active_element(self, sid):
            return None
        async def act(self, sid, op, **kw):
            self.executed.append((sid, op, kw))
            return {"success": True, "url": "https://x.test/done"}
        async def read(self, sid, max_chars=6000):
            return {"success": True, "url": "u", "title": "t", "markdown": "m",
                    "elements": []}

    fake = FakeSessions()
    monkeypatch.setattr(server, "sessions", fake)
    with TestClient(server.app) as c:
        c.fake_sessions = fake
        yield c


def test_gated_click_returns_pending(client):
    r = client.post("/session/psess/click", json={"index": 3})
    body = r.json()
    assert body["status"] == "pending_approval"
    assert client.fake_sessions.executed == []          # nothing ran


def test_anon_click_not_gated(client):
    r = client.post("/session/asess/click", json={"index": 3})
    assert r.json()["success"] is True
    assert ("asess", "click", {"index": 3}) in client.fake_sessions.executed


def test_approve_executes_queued_action(client):
    aid = client.post("/session/psess/click", json={"index": 3}).json()["approval_id"]
    from browser import db
    tok = db.get_approval(aid)["token"]
    r = client.get(f"/approvals/{aid}/decide", params={"tok": tok, "d": "approve"})
    assert r.status_code == 200
    assert db.get_approval(aid)["status"] == "executed"
    assert ("psess", "click", {"index": 3}) in client.fake_sessions.executed


def test_deny_blocks_action(client):
    aid = client.post("/session/psess/click", json={"index": 3}).json()["approval_id"]
    from browser import db
    tok = db.get_approval(aid)["token"]
    client.get(f"/approvals/{aid}/decide", params={"tok": tok, "d": "deny"})
    assert db.get_approval(aid)["status"] == "denied"
    assert client.fake_sessions.executed == []


def test_bad_token_rejected(client):
    aid = client.post("/session/psess/click", json={"index": 3}).json()["approval_id"]
    r = client.get(f"/approvals/{aid}/decide", params={"tok": "wrong", "d": "approve"})
    assert r.status_code == 403


def test_approval_status_endpoint(client):
    aid = client.post("/session/psess/click", json={"index": 3}).json()["approval_id"]
    body = client.get(f"/approvals/{aid}").json()
    assert body["status"] == "pending"
```

- [ ] **Step 6: Run to verify they fail**

Run: `venv/bin/python -m pytest tests/browser/test_server_gate.py -v`
Expected: FAIL — click route has no gate; approval routes 404.

- [ ] **Step 7: Wire the gate into `browser/server.py`**

Import (existing try/except block):

```python
try:
    from browser import gate
except ImportError:  # pragma: no cover
    import gate
```

Replace the bodies of `session_click` and `session_press`:

```python
@app.post("/session/{sid}/click")
async def session_click(sid: str, req: ActReq):
    try:
        s = sessions.get(sid)
        if s.profile:
            el = await sessions.element_info(sid, req.index)
            if gate.is_gated_click(el):
                desc = (f"Agent wants to CLICK [{req.index}] "
                        f"{(el or {}).get('tag', '?')} “{(el or {}).get('text', '?')}” "
                        f"in logged-in profile '{s.profile}' (session {sid}).")
                return gate.request_approval(sid, {"op": "click", "index": req.index}, desc)
        return await sessions.act(sid, "click", index=req.index)
    except KeyError:
        return _no_session(sid)


@app.post("/session/{sid}/press")
async def session_press(sid: str, req: ActReq):
    key = req.key or "Enter"
    try:
        s = sessions.get(sid)
        if s.profile:
            active = await sessions.active_element(sid)
            if gate.is_gated_press(key, active):
                desc = (f"Agent wants to PRESS {key} on "
                        f"“{(active or {}).get('text', '?')}” (POST form) "
                        f"in logged-in profile '{s.profile}' (session {sid}).")
                return gate.request_approval(sid, {"op": "press", "key": key}, desc)
        return await sessions.act(sid, "press", key=key)
    except KeyError:
        return _no_session(sid)
```

Approval routes:

```python
import json as _json2
from fastapi.responses import HTMLResponse


@app.get("/approvals/{aid}")
async def approval_status(aid: int):
    a = db.get_approval(aid)
    if not a:
        return {"success": False, "error": f"no approval {aid}"}
    return {"success": True, "status": a["status"]}


@app.get("/approvals/{aid}/decide")
async def approval_decide(aid: int, tok: str, d: str):
    a = db.get_approval(aid)
    if not a:
        return HTMLResponse("<h2>Unknown approval.</h2>", status_code=404)
    if tok != a["token"]:
        return HTMLResponse("<h2>Bad token.</h2>", status_code=403)
    if a["status"] != "pending":
        return HTMLResponse(f"<h2>Already {a['status']}.</h2>")
    if d != "approve":
        db.decide_approval(aid, "denied")
        return HTMLResponse("<h2>❌ Denied.</h2>")
    db.decide_approval(aid, "approved")
    action = _json2.loads(a["action"])
    try:
        result = await sessions.act(a["session_id"], action.pop("op"), **action)
        db.decide_approval(aid, "executed" if result.get("success") else "error")
        return HTMLResponse(f"<h2>✅ Approved — action executed.</h2>"
                            f"<p>Now at: {result.get('url', '?')}</p>")
    except KeyError:
        db.decide_approval(aid, "error")
        return HTMLResponse("<h2>⚠️ Approved, but the session already expired.</h2>")
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/browser/test_server_gate.py tests/browser/test_gate.py -v`
Expected: 12 passed. Then run the whole browser suite: `venv/bin/python -m pytest tests/browser/ -v` — all pass.

---

### Task 11: `browser/login_helper.py` + profile hygiene

**Files:**
- Create: `browser/login_helper.py`
- Modify: `.gitignore` (repo root), `scripts/claw_fs_watcher.py:46` (SKIP_DIRS)

**Interfaces:**
- Consumes: `browser.engine.profiles_dir`.
- Produces: CLI `venv/bin/python -m browser.login_helper <name> [start-url]` (headed; must run on baza's desktop with a display).

- [ ] **Step 1: Write `browser/login_helper.py`**

```python
"""Seed a logged-in browser profile for Phantom Browser sessions.

RUN ON BAZA'S DESKTOP (needs a display — headed Chromium):

    cd ~/baza-empire/agent-framework-v3
    venv/bin/python -m browser.login_helper gmail https://accounts.google.com

Log in to whatever sites the profile should carry, come back to the terminal
and press Enter. Agents then open sessions with {"profile": "gmail"}.
Only Serge seeds profiles — agents cannot create or modify them."""
import os
import re
import sys

from playwright.sync_api import sync_playwright

try:
    from browser.engine import profiles_dir, UA
except ImportError:  # pragma: no cover
    from engine import profiles_dir, UA


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    name = sys.argv[1]
    if not re.fullmatch(r"[\w\-]+", name):
        print("profile name must be letters/digits/-/_ only")
        return 1
    start_url = sys.argv[2] if len(sys.argv) > 2 else "https://accounts.google.com"
    root = profiles_dir()
    pdir = root / name
    pdir.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    os.chmod(pdir, 0o700)
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(pdir), headless=False, user_agent=UA
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(start_url)
        print(f"\nProfile dir: {pdir}")
        input("Log in in the browser window, then press Enter here to save & close… ")
        ctx.close()
    print(f"Done. Agents can now use sessions with profile: \"{name}\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Keep profiles out of git and Claw**

Append to `$FW/.gitignore`:

```
browser/profiles/
```

Edit `scripts/claw_fs_watcher.py` `SKIP_DIRS` (line 46) — add `"profiles"`:

```python
SKIP_DIRS = {
    ".git", "venv", "__pycache__", "node_modules", "artifacts",
    "logs", "backups", ".private-inbound", ".pytest_cache", "profiles",
}
```

- [ ] **Step 3: Verify**

```bash
cd /home/switchhacker/baza-empire/agent-framework-v3
venv/bin/python -c "from browser import login_helper; print('imports ok')"
mkdir -p browser/profiles && git check-ignore -q browser/profiles/ && echo "gitignored ok"
venv/bin/python -m pytest tests/ -k "claw" -q  # any existing claw tests still pass
```
Expected: `imports ok`, `gitignored ok`, claw tests (if any) pass.

---

### Task 12: systemd unit + watchdog + live deployment

**Files:**
- Create: `configs/baza-phantom-browser.service`
- Modify: `scripts/health_watchdog.sh:18-31` (CRITICAL_SERVICES array)
- Create: `scripts/phantom_browser_smoke.py`

**Interfaces:**
- Produces: live service at `http://localhost:8100` (`baza-phantom-browser.service`), watched by the watchdog; smoke script exits 0 on healthy end-to-end behavior.

- [ ] **Step 1: Write `configs/baza-phantom-browser.service`**

```ini
[Unit]
Description=Phantom Browser — AI web crawler service (:8100)
After=network.target

[Service]
Type=simple
User=switchhacker
WorkingDirectory=/home/switchhacker/baza-empire/agent-framework-v3/browser
Environment=PYTHONPATH=/home/switchhacker/baza-empire/agent-framework-v3
EnvironmentFile=/home/switchhacker/baza-empire/agent-framework-v3/configs/secrets.env
ExecStart=/home/switchhacker/baza-empire/agent-framework-v3/venv/bin/uvicorn server:app --host 0.0.0.0 --port 8100
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Install and start**

```bash
sudo cp /home/switchhacker/baza-empire/agent-framework-v3/configs/baza-phantom-browser.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now baza-phantom-browser.service
sleep 4
curl -s http://localhost:8100/health
```
Expected: `{"ok":true,"service":"phantom-browser"}`. If not, `journalctl -u baza-phantom-browser -n 50`.

- [ ] **Step 3: Add to the watchdog**

In `scripts/health_watchdog.sh`, append to the `CRITICAL_SERVICES` array (line ~30, before the closing paren):

```bash
    baza-phantom-browser.service
```

- [ ] **Step 4: Write `scripts/phantom_browser_smoke.py`**

```python
#!/usr/bin/env python3
"""Live smoke test for Phantom Browser. Hits the real service + real web.
Run: venv/bin/python scripts/phantom_browser_smoke.py"""
import json
import sys
import time

import httpx

BASE = "http://localhost:8100"
FAILS = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")
    if not ok:
        FAILS.append(name)


def main():
    c = httpx.Client(timeout=90)

    r = c.get(f"{BASE}/health").json()
    check("health", r.get("ok") is True)

    r = c.post(f"{BASE}/scrape", json={"url": "https://example.com"}).json()
    check("scrape", r.get("success") and "Example Domain" in r.get("markdown", ""),
          f"title={r.get('title')!r}")

    r = c.post(f"{BASE}/search", json={"query": "home builder pennsylvania", "n": 3}).json()
    check("search", r.get("success") and len(r.get("results", [])) > 0,
          f"{len(r.get('results', []))} results")

    r = c.post(f"{BASE}/map", json={"url": "https://www.iana.org", "limit": 20}).json()
    check("map", r.get("success") and r.get("count", 0) > 0,
          f"{r.get('count')} urls via {r.get('source')}")

    r = c.post(f"{BASE}/crawl", json={"url": "https://example.com", "max_pages": 2}).json()
    jid = r.get("job_id")
    check("crawl start", bool(jid))
    status = None
    for _ in range(30):
        time.sleep(2)
        j = c.get(f"{BASE}/crawl/{jid}").json()
        status = j["job"]["status"]
        if status in ("done", "error"):
            break
    check("crawl finish", status == "done",
          f"status={status}, pages={len(j.get('pages', []))}")

    r = c.post(f"{BASE}/extract", json={
        "url": "https://example.com",
        "schema": {"type": "object", "required": ["heading"],
                   "properties": {"heading": {"type": "string"}}},
        "prompt": "Extract the page's main heading text.",
    }).json()
    check("extract", r.get("success") and "example" in
          json.dumps(r.get("data", {})).lower(), f"data={r.get('data')}")

    sid = c.post(f"{BASE}/session", json={}).json().get("session_id")
    check("session create", bool(sid))
    c.post(f"{BASE}/session/{sid}/goto", json={"url": "https://example.com"})
    read = c.post(f"{BASE}/session/{sid}/read", json={}).json()
    check("session read", read.get("success") and len(read.get("elements", [])) > 0,
          f"{len(read.get('elements', []))} elements")
    link = next((e for e in read.get("elements", []) if e["tag"] == "a"), None)
    if link:
        c.post(f"{BASE}/session/{sid}/click", json={"index": link["idx"]})
        read2 = c.post(f"{BASE}/session/{sid}/read", json={}).json()
        check("session click nav", read2.get("url") != "https://example.com/",
              f"now at {read2.get('url')}")
    c.delete(f"{BASE}/session/{sid}")

    print(f"\n{'ALL PASS' if not FAILS else 'FAILURES: ' + ', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run the smoke test**

Run: `venv/bin/python scripts/phantom_browser_smoke.py`
Expected: `ALL PASS` (extract needs the local ollama :11434 up with `glm-4.7-flash`; search needs the SearXNG container from Task 5).

---

### Task 13: Agent-facing skills (6 files)

**Files:**
- Create: `skills/shared/browse.py`, `skills/shared/web_scrape.py`, `skills/shared/crawl_site.py`, `skills/shared/web_map.py`, `skills/shared/web_extract.py`
- Test: `tests/browser/test_skills.py` (subprocess pattern, error path via dead port + happy path mocked by a tiny local HTTP stub)

**Interfaces:**
- Consumes: the :8100 API (env `PHANTOM_BROWSER_URL`, default `http://localhost:8100`).
- Produces: skills callable as `##SKILL:browse{...}##` etc. All print JSON to stdout. `browse` auto-creates a session on `goto` without `session_id` and always echoes `session_id` back.

- [ ] **Step 1: Write the failing tests** — `tests/browser/test_skills.py`

```python
import http.server
import json
import os
import subprocess
import sys
import threading

FRAMEWORK = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SKILLS = os.path.join(FRAMEWORK, "skills", "shared")


def run_skill(name, args, base_url):
    env = dict(os.environ)
    env["SKILL_ARGS"] = json.dumps(args)
    env["PHANTOM_BROWSER_URL"] = base_url
    return subprocess.run([sys.executable, os.path.join(SKILLS, name)],
                          capture_output=True, text=True, env=env, timeout=30)


class Stub(http.server.BaseHTTPRequestHandler):
    """Fake :8100 — answers every POST with a canned JSON body per path."""
    responses = {
        "/session": {"success": True, "session_id": "abc123", "profile": None},
        "/session/abc123/goto": {"success": True, "url": "https://s.test/"},
        "/session/abc123/read": {"success": True, "url": "https://s.test/",
                                 "title": "S", "markdown": "# S",
                                 "elements": [{"idx": 0, "tag": "a", "type": "",
                                               "text": "Next", "in_form": False,
                                               "form_method": ""}]},
        "/scrape": {"success": True, "url": "u", "title": "T", "markdown": "# T",
                    "links": [], "truncated": False},
        "/map": {"success": True, "count": 1, "urls": ["https://s.test/a"],
                 "source": "sitemap"},
        "/crawl": {"success": True, "job_id": 7},
        "/crawl/7": {"success": True, "job": {"status": "done"},
                     "pages": [{"url": "https://s.test/", "title": "S",
                                "markdown": "# S", "status": "ok"}]},
        "/extract": {"success": True, "data": {"x": 1}, "sources": ["u"]},
    }

    def _reply(self):
        body = self.responses.get(self.path)
        if body is None:
            self.send_response(404); self.end_headers(); return
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self._reply()

    def do_GET(self):
        self._reply()

    def log_message(self, *a):
        pass


def _start_stub():
    srv = http.server.HTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


def test_browse_goto_auto_session():
    srv, base = _start_stub()
    try:
        p = run_skill("browse.py", {"action": "goto", "url": "https://s.test/"}, base)
        out = json.loads(p.stdout)
        assert out["success"] is True and out["session_id"] == "abc123"
    finally:
        srv.shutdown()


def test_browse_read_lists_elements():
    srv, base = _start_stub()
    try:
        p = run_skill("browse.py", {"action": "read", "session_id": "abc123"}, base)
        out = json.loads(p.stdout)
        assert out["elements"][0]["text"] == "Next"
    finally:
        srv.shutdown()


def test_web_scrape_happy():
    srv, base = _start_stub()
    try:
        p = run_skill("web_scrape.py", {"url": "https://s.test/"}, base)
        out = json.loads(p.stdout)
        assert out["success"] is True and out["markdown"] == "# T"
    finally:
        srv.shutdown()


def test_crawl_site_polls_to_done():
    srv, base = _start_stub()
    try:
        p = run_skill("crawl_site.py", {"url": "https://s.test/"}, base)
        out = json.loads(p.stdout)
        assert out["job"]["status"] == "done" and len(out["pages"]) == 1
    finally:
        srv.shutdown()


def test_web_map_and_extract():
    srv, base = _start_stub()
    try:
        m = json.loads(run_skill("web_map.py", {"url": "https://s.test/"}, base).stdout)
        assert m["urls"] == ["https://s.test/a"]
        e = json.loads(run_skill("web_extract.py",
                                 {"url": "u", "schema": {"type": "object"}}, base).stdout)
        assert e["data"] == {"x": 1}
    finally:
        srv.shutdown()


def test_service_down_is_graceful():
    p = run_skill("web_scrape.py", {"url": "https://x.com"}, "http://localhost:9")
    out = json.loads(p.stdout)
    assert out["success"] is False and "phantom-browser" in out["hint"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `venv/bin/python -m pytest tests/browser/test_skills.py -v`
Expected: FAIL — skill files don't exist (FileNotFoundError).

- [ ] **Step 3: Write the five skills**

`skills/shared/browse.py`:

```python
#!/usr/bin/env python3
"""Interactive web browsing via the Phantom Browser service (:8100).
Multistep: goto creates a session, read returns the page as markdown plus
NUMBERED interactive elements, then click/type by element index."""
SKILL_META = {
    "category": "web",
    "summary": "Interactive browser session: goto/read/click/type/press/scroll/back/screenshot/close.",
    "when_to_use": ("When a task needs real browsing — JS-heavy pages, walking search "
                    "results, pagination, forms, logged-in sites. Start with "
                    "{\"action\":\"goto\",\"url\":...}; then {\"action\":\"read\"} to see the page "
                    "and its numbered elements; then act by index. Always pass back session_id."),
    "args": {
        "action": "goto|read|click|type|press|scroll|back|screenshot|close|approval_status",
        "session_id": "returned by the first goto — pass it on every later call",
        "url": "for goto", "index": "element index from read (for click/type)",
        "text": "for type", "key": "for press (default Enter)", "dy": "scroll pixels",
        "profile": "optional logged-in profile name (write actions need Serge's approval)",
        "approval_id": "for approval_status",
        "max_chars": "read: markdown size cap (default 6000)",
    },
}
import json
import os
import sys

try:
    args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
except json.JSONDecodeError as e:
    print(f"browse: invalid SKILL_ARGS JSON: {e}")
    sys.exit(1)

import httpx

BASE = os.environ.get("PHANTOM_BROWSER_URL", "http://localhost:8100")
action = args.get("action", "")
sid = args.get("session_id", "")


def call(method, path, payload=None):
    r = httpx.request(method, f"{BASE}{path}", json=payload, timeout=60)
    r.raise_for_status()
    return r.json()


try:
    if action == "goto" and not sid:
        sid = call("POST", "/session", {"profile": args.get("profile")})["session_id"]

    if action == "close":
        out = call("DELETE", f"/session/{sid}")
    elif action == "approval_status":
        out = call("GET", f"/approvals/{int(args.get('approval_id', 0))}")
    elif action in ("goto", "read", "click", "type", "press", "scroll", "back",
                    "screenshot"):
        payload = {k: args.get(k) for k in ("url", "index", "text", "key", "dy")
                   if args.get(k) is not None}
        if action == "read":
            payload["max_chars"] = int(args.get("max_chars", 6000))
        out = call("POST", f"/session/{sid}/{action}", payload)
    else:
        out = {"success": False,
               "error": f"unknown action '{action}'",
               "hint": "actions: goto/read/click/type/press/scroll/back/screenshot/close/approval_status"}
    out["session_id"] = sid
    print(json.dumps(out))
except httpx.HTTPError as e:
    print(json.dumps({"success": False, "session_id": sid,
                      "error": f"{type(e).__name__}: {e}",
                      "hint": "is baza-phantom-browser.service running on :8100?"}))
```

`skills/shared/web_scrape.py`:

```python
#!/usr/bin/env python3
"""Scrape any URL (JS-rendered, headless Chromium) into clean markdown via the
Phantom Browser service. Successor to scrape_page for real pages."""
SKILL_META = {
    "category": "web",
    "summary": "Render a URL in a real browser and return clean markdown + links.",
    "when_to_use": ("To read any web page — including JS-heavy/SPA pages that plain "
                    "HTTP fetch can't render. Returns markdown, title, links."),
    "args": {"url": "page to scrape (required)",
             "max_chars": "markdown cap, default 8000",
             "wait_ms": "extra wait after load for slow JS, default 0",
             "screenshot": "true to also save a PNG"},
}
import json
import os
import sys

try:
    args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
except json.JSONDecodeError as e:
    print(f"web_scrape: invalid SKILL_ARGS JSON: {e}")
    sys.exit(1)

import httpx

BASE = os.environ.get("PHANTOM_BROWSER_URL", "http://localhost:8100")
url = args.get("url", "")
if not url:
    print(json.dumps({"success": False, "error": "url is required"}))
    sys.exit(1)

try:
    r = httpx.post(f"{BASE}/scrape", json={
        "url": url,
        "max_chars": int(args.get("max_chars", 8000)),
        "wait_ms": int(args.get("wait_ms", 0)),
        "screenshot": bool(args.get("screenshot", False)),
    }, timeout=90)
    r.raise_for_status()
    print(json.dumps(r.json()))
except httpx.HTTPError as e:
    print(json.dumps({"success": False, "url": url,
                      "error": f"{type(e).__name__}: {e}",
                      "hint": "is baza-phantom-browser.service running on :8100?"}))
```

`skills/shared/crawl_site.py`:

```python
#!/usr/bin/env python3
"""Crawl a whole site/section into markdown pages via the Phantom Browser
service. Starts an async BFS job and polls it (up to ~75s inside the skill;
longer crawls: re-call with job_id to keep polling)."""
SKILL_META = {
    "category": "web",
    "summary": "BFS-crawl a site (or path subset) into markdown pages.",
    "when_to_use": ("To gather MANY pages from one site — docs sections, competitor "
                    "sites, catalogs. For one page use web_scrape. Re-call with "
                    "job_id if status is still 'running'."),
    "args": {"url": "start URL (required unless job_id)",
             "job_id": "poll an existing crawl instead of starting a new one",
             "max_pages": "default 50", "max_depth": "default 3",
             "include_paths": "list of regexes paths must match",
             "exclude_paths": "list of regexes to skip",
             "max_chars": "markdown cap per page, default 3000"},
}
import json
import os
import sys
import time

try:
    args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
except json.JSONDecodeError as e:
    print(f"crawl_site: invalid SKILL_ARGS JSON: {e}")
    sys.exit(1)

import httpx

BASE = os.environ.get("PHANTOM_BROWSER_URL", "http://localhost:8100")

try:
    job_id = args.get("job_id")
    if not job_id:
        if not args.get("url"):
            print(json.dumps({"success": False, "error": "url or job_id required"}))
            sys.exit(1)
        payload = {k: args[k] for k in ("url", "max_pages", "max_depth",
                                        "include_paths", "exclude_paths",
                                        "max_chars", "same_domain") if k in args}
        r = httpx.post(f"{BASE}/crawl", json=payload, timeout=30)
        r.raise_for_status()
        job_id = r.json()["job_id"]

    deadline = time.time() + 75
    body = None
    while time.time() < deadline:
        r = httpx.get(f"{BASE}/crawl/{job_id}", timeout=30)
        r.raise_for_status()
        body = r.json()
        if not body.get("success") or body["job"]["status"] in ("done", "error"):
            break
        time.sleep(3)
    body = body or {"success": False, "error": "no response"}
    body["job_id"] = job_id
    if body.get("job", {}).get("status") == "running":
        body["hint"] = f"crawl still running — call crawl_site again with job_id {job_id}"
    print(json.dumps(body))
except httpx.HTTPError as e:
    print(json.dumps({"success": False, "error": f"{type(e).__name__}: {e}",
                      "hint": "is baza-phantom-browser.service running on :8100?"}))
```

`skills/shared/web_map.py`:

```python
#!/usr/bin/env python3
"""List a site's URLs (sitemap-first, link sweep fallback) via the Phantom
Browser service — pick targets before scraping/crawling."""
SKILL_META = {
    "category": "web",
    "summary": "Discover a site's URLs (sitemap or link sweep).",
    "when_to_use": ("Before crawling/scraping a site: get the URL inventory, then "
                    "web_scrape the interesting ones or crawl_site a subset."),
    "args": {"url": "site root or any page (required)", "limit": "default 200"},
}
import json
import os
import sys

try:
    args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
except json.JSONDecodeError as e:
    print(f"web_map: invalid SKILL_ARGS JSON: {e}")
    sys.exit(1)

import httpx

BASE = os.environ.get("PHANTOM_BROWSER_URL", "http://localhost:8100")
url = args.get("url", "")
if not url:
    print(json.dumps({"success": False, "error": "url is required"}))
    sys.exit(1)

try:
    r = httpx.post(f"{BASE}/map", json={"url": url, "limit": int(args.get("limit", 200))},
                   timeout=60)
    r.raise_for_status()
    print(json.dumps(r.json()))
except httpx.HTTPError as e:
    print(json.dumps({"success": False, "url": url,
                      "error": f"{type(e).__name__}: {e}",
                      "hint": "is baza-phantom-browser.service running on :8100?"}))
```

`skills/shared/web_extract.py`:

```python
#!/usr/bin/env python3
"""Extract structured JSON from web pages via the Phantom Browser service —
scrape → LOCAL Ollama model → JSON validated against your schema."""
SKILL_META = {
    "category": "web",
    "summary": "Scrape page(s) and extract JSON matching a schema (local LLM).",
    "when_to_use": ("When you need specific fields off a page — prices, specs, "
                    "contact info, listings — as clean JSON instead of prose."),
    "args": {"url": "single page", "urls": "list of pages (max 5)",
             "content": "raw text instead of a url",
             "schema": "JSON schema of the wanted object (required)",
             "prompt": "optional extra extraction instructions"},
}
import json
import os
import sys

try:
    args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
except json.JSONDecodeError as e:
    print(f"web_extract: invalid SKILL_ARGS JSON: {e}")
    sys.exit(1)

import httpx

BASE = os.environ.get("PHANTOM_BROWSER_URL", "http://localhost:8100")
if not args.get("schema"):
    print(json.dumps({"success": False, "error": "schema is required"}))
    sys.exit(1)

try:
    payload = {k: args[k] for k in ("url", "urls", "content", "schema", "prompt", "model")
               if k in args}
    r = httpx.post(f"{BASE}/extract", json=payload, timeout=240)
    r.raise_for_status()
    print(json.dumps(r.json()))
except httpx.HTTPError as e:
    print(json.dumps({"success": False, "error": f"{type(e).__name__}: {e}",
                      "hint": "is baza-phantom-browser.service running on :8100?"}))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/browser/test_skills.py -v`
Expected: 7 passed.

---

### Task 14: Rewire legacy paths (web_search → SearXNG, shims, tool-server, scaffold)

**Files:**
- Modify: `skills/shared/web_search.py` (SearXNG primary, DDG fallback, drop dead ollama path)
- Rewrite: `skills/shared/web_fetch.py`, `skills/shared/scrape_page.py` (compat shims → `/scrape`, urllib fallback)
- Modify: `tools/server.py:306-364` (both sam routes → :8100)
- Modify: `config/scaffold.yaml` (pinned_core += web_scrape)
- Modify: `core/base_agent.py:168` (web_fetch description text)
- Test: `tests/browser/test_rewired_skills.py`

**Interfaces:**
- Consumes: `/scrape` and `/search` on :8100; `SEARXNG_URL` env.
- Produces: unchanged historical output shapes — `web_search` text/json modes with `results: [{title,url,snippet}]` and `source` string; `scrape_page` json keys `{success,url,title,text,chars}`; `web_fetch` json keys `{success,url,title,content,chars}`.

- [ ] **Step 1: Write the failing tests** — `tests/browser/test_rewired_skills.py`

```python
import json
import os
import subprocess
import sys

FRAMEWORK = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SKILLS = os.path.join(FRAMEWORK, "skills", "shared")


def run_skill(name, args, env_extra=None):
    env = dict(os.environ)
    env["SKILL_ARGS"] = json.dumps(args)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, os.path.join(SKILLS, name)],
                          capture_output=True, text=True, env=env, timeout=30)


def test_web_search_searxng_primary(tmp_path):
    # stub searxng via the same http stub pattern as test_skills
    from tests.browser.test_skills import Stub, _start_stub

    class SearxStub(Stub):
        responses = {"/search": {"query": "q", "results": [
            {"title": "A", "url": "https://a.test", "content": "snip"}]}}

    import http.server, threading
    srv = http.server.HTTPServer(("127.0.0.1", 0), SearxStub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        p = run_skill("web_search.py", {"query": "q", "output": "json"},
                      {"SEARXNG_URL": f"http://127.0.0.1:{srv.server_port}"})
        out = json.loads(p.stdout)
        assert out["source"] == "searxng"
        assert out["results"][0] == {"title": "A", "url": "https://a.test",
                                     "snippet": "snip"}
    finally:
        srv.shutdown()


def test_web_search_falls_back_to_ddg_source_label():
    # searxng unreachable → source must say duckduckgo (network may or may not
    # work in test env; only assert the source label + valid json shape)
    p = run_skill("web_search.py", {"query": "q", "output": "json"},
                  {"SEARXNG_URL": "http://localhost:9"})
    out = json.loads(p.stdout)
    assert "duckduckgo" in out["source"]


def test_scrape_page_shim_keys():
    from tests.browser.test_skills import Stub
    import http.server, threading

    class ScrapeStub(Stub):
        responses = {"/scrape": {"success": True, "url": "https://s.test/",
                                 "title": "T", "markdown": "body text",
                                 "links": [], "truncated": False}}

    srv = http.server.HTTPServer(("127.0.0.1", 0), ScrapeStub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        p = run_skill("scrape_page.py", {"url": "https://s.test/", "output": "json"},
                      {"PHANTOM_BROWSER_URL": f"http://127.0.0.1:{srv.server_port}"})
        out = json.loads(p.stdout)
        assert out == {"success": True, "url": "https://s.test/", "title": "T",
                       "text": "body text", "chars": len("body text")}
    finally:
        srv.shutdown()


def test_web_fetch_shim_keys():
    from tests.browser.test_skills import Stub
    import http.server, threading

    class ScrapeStub(Stub):
        responses = {"/scrape": {"success": True, "url": "https://s.test/",
                                 "title": "T", "markdown": "body text",
                                 "links": [], "truncated": False}}

    srv = http.server.HTTPServer(("127.0.0.1", 0), ScrapeStub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        p = run_skill("web_fetch.py", {"url": "https://s.test/", "output": "json"},
                      {"PHANTOM_BROWSER_URL": f"http://127.0.0.1:{srv.server_port}"})
        out = json.loads(p.stdout)
        assert out["content"] == "body text" and out["success"] is True
    finally:
        srv.shutdown()
```

- [ ] **Step 2: Run to verify they fail**

Run: `venv/bin/python -m pytest tests/browser/test_rewired_skills.py -v`
Expected: FAIL (web_search has no searxng path; shims not rewritten).

- [ ] **Step 3: Rewire `skills/shared/web_search.py`**

Add after the existing imports (keep `ddg_search` exactly as-is; delete the `ollama_search` function and the `_HAS_OLLAMA` import block):

```python
def searxng_search(query: str, max_results: int = 5) -> list:
    """Primary: self-hosted SearXNG meta-search (local-first, no API key)."""
    base = os.environ.get("SEARXNG_URL", "http://localhost:8181")
    try:
        req = urllib.request.Request(
            f"{base}/search?q={urllib.parse.quote(query)}&format=json",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=12) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        return [
            {"title": x.get("title", ""), "url": x.get("url", ""),
             "snippet": (x.get("content") or "")[:300]}
            for x in data.get("results", [])[:max_results]
        ]
    except Exception as e:
        return [{"error": f"searxng: {e}"}]
```

Replace the selection block (currently `api_key = ...` through the final scrub) with:

```python
results = searxng_search(query, n)
source = "searxng"
real = [r for r in results if not (len(r) == 1 and "error" in r)]
if not real:
    results = ddg_search(query, n)
    source = "duckduckgo (searxng down)"
else:
    results = real

# Final scrub of error-only entries
results = [r for r in results if not (len(r) == 1 and "error" in r)]
```

(`urllib.parse` must be imported — the file already imports `urllib.request`; add `import urllib.parse` beside it if missing.) Keep the output block unchanged.

- [ ] **Step 4: Rewrite `skills/shared/scrape_page.py`** (keep name + output shape; body becomes)

```python
#!/usr/bin/env python3
"""Fetch a page as text. Now backed by the Phantom Browser service (:8100,
real Chromium render); falls back to plain urllib if the service is down.
Kept for prompt-compat — new work should call web_scrape."""
SKILL_META = {
    "category": "web",
    "summary": "Fetch a URL's text content (browser-rendered; urllib fallback).",
    "when_to_use": "Legacy alias — prefer web_scrape for markdown + links.",
    "args": {"url": "required", "max_chars": "default 4000",
             "output": "text|json"},
}
import json
import os
import re
import sys
import urllib.request

try:
    args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
except json.JSONDecodeError as e:
    print(f"scrape_page: invalid SKILL_ARGS JSON: {e}")
    sys.exit(1)

url = args.get("url", "")
max_chars = int(args.get("max_chars", 4000))
output = args.get("output", "text")

if not url:
    print(json.dumps({"success": False, "error": "url is required"}))
    sys.exit(1)


def via_phantom_browser() -> dict | None:
    try:
        import httpx
        r = httpx.post(
            f"{os.environ.get('PHANTOM_BROWSER_URL', 'http://localhost:8100')}/scrape",
            json={"url": url, "max_chars": max_chars}, timeout=90)
        r.raise_for_status()
        d = r.json()
        if not d.get("success"):
            return None
        text = d.get("markdown", "")
        return {"success": True, "url": url, "title": d.get("title", ""),
                "text": text, "chars": len(text)}
    except Exception:
        return None


def via_urllib() -> dict:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            html = r.read().decode("utf-8", "replace")
        text = re.sub(r"(?is)<(script|style|noscript|nav|footer|header)[^>]*>.*?</\1>",
                      " ", html)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()[:max_chars]
        m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
        title = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
        return {"success": True, "url": url, "title": title,
                "text": text, "chars": len(text)}
    except Exception as e:
        return {"success": False, "url": url, "error": f"{type(e).__name__}: {e}"}


result = via_phantom_browser() or via_urllib()

if output == "json":
    print(json.dumps(result))
elif result.get("success"):
    print(f"PAGE: {result['title']}\nURL: {result['url']}\nCHARS: {result['chars']}\n"
          + "-" * 40 + f"\n{result['text']}")
else:
    print(f"ERROR: {result.get('error')}", file=sys.stderr)
    sys.exit(1)
```

- [ ] **Step 5: Rewrite `skills/shared/web_fetch.py`** — same structure as scrape_page shim but historical keys (`content` not `text`), default `max_chars` 8000:

```python
#!/usr/bin/env python3
"""Fetch full page content. Now backed by the Phantom Browser service (:8100,
real Chromium render); falls back to plain urllib. Kept because
core/base_agent.py exposes self.web_fetch() and prompts reference it."""
SKILL_META = {
    "category": "web",
    "summary": "Fetch a URL's full content (browser-rendered; urllib fallback).",
    "when_to_use": "Legacy alias — prefer web_scrape for markdown + links.",
    "args": {"url": "required", "max_chars": "default 8000",
             "output": "text|json"},
}
import json
import os
import re
import sys
import urllib.request

try:
    args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
except json.JSONDecodeError as e:
    print(f"web_fetch: invalid SKILL_ARGS JSON: {e}")
    sys.exit(1)

url = args.get("url", "")
max_chars = int(args.get("max_chars", 8000))
output = args.get("output", "text")

if not url:
    print(json.dumps({"success": False, "error": "url is required"}))
    sys.exit(1)


def via_phantom_browser() -> dict | None:
    try:
        import httpx
        r = httpx.post(
            f"{os.environ.get('PHANTOM_BROWSER_URL', 'http://localhost:8100')}/scrape",
            json={"url": url, "max_chars": max_chars}, timeout=90)
        r.raise_for_status()
        d = r.json()
        if not d.get("success"):
            return None
        content = d.get("markdown", "")
        return {"success": True, "url": url, "title": d.get("title", ""),
                "content": content, "chars": len(content),
                "links": d.get("links", [])}
    except Exception:
        return None


def via_urllib() -> dict:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            html = r.read().decode("utf-8", "replace")
        content = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
        content = re.sub(r"(?s)<[^>]+>", " ", content)
        content = re.sub(r"\s+", " ", content).strip()[:max_chars]
        m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
        title = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
        return {"success": True, "url": url, "title": title,
                "content": content, "chars": len(content), "links": []}
    except Exception as e:
        return {"success": False, "url": url, "error": f"{type(e).__name__}: {e}"}


result = via_phantom_browser() or via_urllib()

if output == "json":
    print(json.dumps(result))
elif result.get("success"):
    print(f"PAGE: {result['title']}\nURL: {result['url']}\nCHARS: {result['chars']}\n"
          + "-" * 40 + f"\n{result['content']}")
else:
    print(f"ERROR: {result.get('error')}", file=sys.stderr)
    sys.exit(1)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/browser/test_rewired_skills.py -v`
Expected: 4 passed.

- [ ] **Step 7: Repoint `tools/server.py` sam routes**

In `sam_scrape_web` (line ~306), replace the `_run` body:

```python
    async def _run(inp):
        url = inp.get("url", "")
        if not url:
            raise ValueError("No URL provided")
        pb = os.environ.get("PHANTOM_BROWSER_URL", "http://localhost:8100")
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(f"{pb}/scrape",
                                     json={"url": url, "max_chars": 5000})
            resp.raise_for_status()
            data = resp.json()
        if not data.get("success"):
            raise ValueError(data.get("error", "scrape failed"))
        return {"url": url, "content": data["markdown"],
                "length": len(data["markdown"]), "title": data.get("title", "")}
```

In `sam_market_research` (line ~334), replace the `_run` body:

```python
    async def _run(inp):
        query = inp.get("query", "")
        if not query:
            raise ValueError("No query provided")
        pb = os.environ.get("PHANTOM_BROWSER_URL", "http://localhost:8100")
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(f"{pb}/search", json={"query": query, "n": 5})
            resp.raise_for_status()
            data = resp.json()
        if not data.get("success"):
            raise ValueError(data.get("error", "search failed"))
        return {"query": query, "results": data["results"]}
```

- [ ] **Step 8: `config/scaffold.yaml` + `core/base_agent.py` text**

In `config/scaffold.yaml`, `pinned_core` becomes:

```yaml
  pinned_core:              # always-on skills (injected every request when enabled)
    - artifact_save
    - web_search
    - web_scrape
    - ahb123_query
    - skill_search
    - call_tool
```

In `core/base_agent.py` line 168, change:

```python
            "##SKILL:web_fetch{\"url\": \"...\", \"max_chars\": 8000}## → fetch full page content via Ollama\n"
```
to:

```python
            "##SKILL:web_fetch{\"url\": \"...\", \"max_chars\": 8000}## → fetch rendered page content (Phantom Browser)\n"
            "##SKILL:browse{\"action\": \"goto\", \"url\": \"...\"}## → interactive browser session (then read/click/type by index)\n"
```

- [ ] **Step 9: Restart tool server + verify no regressions**

```bash
sudo systemctl restart baza-tool-server.service
sleep 3
curl -s -X POST http://localhost:8000/tools/sam/scrape-web \
  -H 'Content-Type: application/json' \
  -d '{"input": {"url": "https://example.com"}}' | head -c 300
venv/bin/python -m pytest tests/ -q -m "not integration" 2>&1 | tail -5
```
Expected: scrape-web returns `"success":true` with markdown content; full non-integration test suite passes (no regressions elsewhere).

---

### Task 15: Registry rebuild, agent restarts, Specter repoint, docs

**Files:**
- Modify: `agents/specter_voss/openclaw/config.yaml:81-84` (browser section)
- Modify: `CLAUDE.md` (services block + data layer)
- Run: registry rebuild + service restarts + phantom rsync

**Interfaces:**
- Produces: all 9 agents see the new skills; Specter config points at baza :8100.

- [ ] **Step 1: Rebuild the skill registry and verify FTS finds the kit**

```bash
cd /home/switchhacker/baza-empire/agent-framework-v3
venv/bin/python -m core.skill_registry --build
venv/bin/python - <<'EOF'
from core import skill_registry as reg
hits = {h["name"] for h in reg.search("browse web page interactively click")}
assert "browse" in hits, hits
hits2 = {h["name"] for h in reg.search("crawl site pages")}
assert "crawl_site" in hits2, hits2
print("registry ok:", sorted(hits | hits2))
EOF
```
Expected: `registry ok: [...]` listing browse/crawl_site among results.

- [ ] **Step 2: Restart the 8 baza agents** (picks up base_agent prompt change + scaffold pin)

```bash
for a in simon-bately claw-batto phil-hass sam-axe rex-valor duke-harmon scout-reeves nova-sterling; do
  sudo systemctl restart baza-agent-$a.service
done
systemctl --no-pager --failed | head -5
```
Expected: no failed units.

- [ ] **Step 3: Repoint Specter's browser config + sync to phantom**

Edit `agents/specter_voss/openclaw/config.yaml` lines 81-84 from:

```yaml
  # Browser automation
  browser:
    enabled: true
    engine: "browser-use"
    headless: true
```
to:

```yaml
  # Browser automation — Phantom Browser service on baza (Tailscale)
  browser:
    enabled: true
    engine: "phantom-browser"
    url: "http://100.127.118.103:8100"
    headless: true
```

Then sync and restart:

```bash
rsync -av /home/switchhacker/baza-empire/agent-framework-v3/agents/specter_voss/openclaw/config.yaml \
  phantom:/home/switchhacker/baza-empire/agent-framework-v3/agents/specter_voss/openclaw/config.yaml
rsync -av /home/switchhacker/baza-empire/agent-framework-v3/skills/shared/ \
  phantom:/home/switchhacker/baza-empire/agent-framework-v3/skills/shared/
ssh phantom sudo systemctl restart baza-specter.service
ssh phantom systemctl is-active baza-specter.service
```
Expected: `active`.

- [ ] **Step 4: Update `CLAUDE.md`**

In the "Core platform" services block, after the `baza-litellm` line, add:

```bash
sudo systemctl restart baza-phantom-browser.service   # Phantom Browser crawler kit :8100 (Playwright)
```

In the "Data layer (SQLite files)" section add:

```markdown
- `baza-empire/agent-framework-v3/dashboard/phantom_browser.db` — Phantom Browser crawl jobs, write-gate approvals, page cache.
```

After the services block's ollama section, add one line to Hosts/Services notes where SearXNG fits naturally (docker services):

```markdown
SearXNG (docker, `~/searxng/`) on :8181 — local meta-search backing web_search + /search. `cd ~/searxng && docker compose up -d`.
```

- [ ] **Step 5: End-to-end agent verification**

```bash
cd /home/switchhacker/baza-empire/agent-framework-v3
venv/bin/python - <<'EOF'
import json, os, subprocess, sys
env = dict(os.environ)
env["SKILL_ARGS"] = json.dumps({"query": "AHB123 competitors bucks county builders", "n": 3, "output": "json"})
p = subprocess.run([sys.executable, "skills/shared/web_search.py"], capture_output=True, text=True, env=env, timeout=60)
out = json.loads(p.stdout)
print("search source:", out["source"], "| results:", len(out["results"]))
assert out["source"] == "searxng" and out["results"], "searxng not primary!"
EOF
venv/bin/python scripts/phantom_browser_smoke.py
```
Expected: `search source: searxng | results: 3` and smoke `ALL PASS`.

- [ ] **Step 6: Session log**

Append the completion entry to `~/Desktop/baza-session-log.md` (timestamp from `date '+%Y-%m-%d %H:%M'`) summarizing: service live on :8100, SearXNG :8181, 6 skills registered, legacy paths rewired, Specter repointed, smoke results.

---

## Verification checklist (whole feature)

1. `venv/bin/python -m pytest tests/browser/ -v` — all green (unit + integration).
2. `venv/bin/python scripts/phantom_browser_smoke.py` — `ALL PASS`.
3. `curl -s http://localhost:8100/health` — ok.
4. `systemctl is-active baza-phantom-browser searxng`-equivalents active (`docker ps` shows searxng).
5. Ask one agent on Telegram to research something requiring browsing (e.g. Duke: "find three Bucks County builder websites and summarize their service pages") — watch it chain `web_search` → `web_scrape`/`browse` in the loop.
6. Write-gate live check: seed a throwaway profile, open a session with it, click a submit-ish element, confirm the Telegram approval message arrives, confirm 5-min silence denies.
