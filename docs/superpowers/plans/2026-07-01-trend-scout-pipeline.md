# Trend-Scout Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agents research AI/tech + AHBCO business trends on a schedule, propose concrete ideas, and Serge approves ideas into real tasks via Telegram inline buttons — plus targeted hardening of the task/event/skill plumbing the pipeline rides on.

**Architecture:** A 4-hourly scanner fetches sources from a YAML registry, scores new items with local Ollama into a new `dashboard/trend_scout.db`; a daily idea round runs each beat's owning agent persona over its top items to generate proposals; Scout's bot sends a digest with ✅/❌ inline buttons; approve creates a task in `baza_projects.db` that the existing `core/task_runner.py` executes. Hardening: task leases, anchored completion-signal parsing, skills-engine guards, event-name constants.

**Tech Stack:** Python 3 (existing `venv/`), SQLite (WAL), feedparser (new dep), requests, PyYAML, python-telegram-bot v20+ (already installed), local Ollama (`qwen2.5:14b` scoring, `gpt-oss:20b` ideas), Redis event bus, systemd timers.

**Spec:** `docs/superpowers/specs/2026-07-01-trend-scout-pipeline-design.md`

## Global Constraints

- **Local-first (HARD rule):** every LLM call goes to `http://localhost:11434` (or `OLLAMA_URL`). No cloud APIs anywhere in this feature.
- **Working directory:** `/home/switchhacker/baza-empire/agent-framework-v3` — all paths below are relative to it. Run everything with `venv/bin/python` / `venv/bin/pytest`.
- **Existing tests must stay green.** After each task also run the module-scoped tests you touched; Task 12 runs the full suite.
- **New DB:** `dashboard/trend_scout.db` only. The only writes to `baza_projects.db` are the `projects`/`tasks` rows created on approval.
- **Timestamps:** UTC, format `YYYY-MM-DD HH:MM:SS` (comparable with SQLite `datetime('now')` — do NOT use ISO `T` separator).
- **Env overrides (all optional):** `BAZA_TREND_DB`, `BAZA_TREND_SOURCES`, `TREND_SCORING_MODEL` (default `qwen2.5:14b`), `TREND_IDEA_MODEL` (default `gpt-oss:20b`), `OLLAMA_URL` (default `http://localhost:11434`), `TELEGRAM_SCOUT_REEVES`, `SERGE_CHAT_ID` (default `8551331144`).
- **Commits:** commit per task with `git add <specific files>` (never `git add -A` — the hourly `claw-auto-git` timer also commits this tree; targeted adds avoid sweeping unrelated files in).
- **Tests that would touch PostgreSQL (`context_db`), Redis (`event_bus`), or Telegram must monkeypatch those calls** — patterns are given in each task.
- Do not edit `dashboard/templates/` or anything in `agent-framework-v2/`.

---

### Task 1: Trend storage module (`core/trend_db.py`)

**Files:**
- Create: `core/trend_db.py`
- Test: `tests/test_trend_db.py`

**Interfaces:**
- Consumes: nothing (stdlib + sqlite3 only).
- Produces (used by Tasks 4–7):
  - `init_db() -> None`
  - `url_hash(url: str) -> str` (16-hex)
  - `is_seen(url: str) -> bool`, `mark_seen(url: str) -> bool` (True if newly inserted)
  - `add_trend_item(url, title, source, beat, score, summary="", published_at=None) -> int` (rowid)
  - `get_items(ids: list[int]) -> list[dict]`
  - `top_items(beat: str, limit: int = 8) -> list[dict]` (unused items, score desc)
  - `beats_with_pending_items() -> list[str]`
  - `mark_items_used(ids: list[int]) -> None`
  - `source_ok(name)`, `source_fail(name, err)`, `failing_sources(threshold=5) -> list[dict]`
  - `add_proposal(beat, agent_id, title, rationale, impact, effort, suggested_assignee, cited_item_ids) -> str | None` (None if duplicate)
  - `get_proposal(pid) -> dict | None`
  - `proposals_for_digest(limit=5) -> list[dict]` (beat round-robin over status='proposed')
  - `set_status(pid, status, task_id=None) -> bool` (guarded transitions; atomic)
  - `set_task_id(pid, task_id) -> None`
  - `expire_stale(days=14) -> int`
  - DB path resolved per-call from `BAZA_TREND_DB` env, default `dashboard/trend_scout.db`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_trend_db.py`:

```python
import pytest
from core import trend_db


@pytest.fixture(autouse=True)
def tmp_trend_db(tmp_path, monkeypatch):
    monkeypatch.setenv("BAZA_TREND_DB", str(tmp_path / "trend_scout.db"))
    trend_db.init_db()


def test_mark_seen_dedupes():
    assert trend_db.mark_seen("https://x.com/a") is True
    assert trend_db.mark_seen("https://x.com/a") is False
    assert trend_db.is_seen("https://x.com/a") is True
    assert trend_db.is_seen("https://x.com/b") is False


def test_add_and_top_items():
    lo = trend_db.add_trend_item("https://x/1", "Low item", "src", "local-ai", 6.0)
    hi = trend_db.add_trend_item("https://x/2", "High item", "src", "local-ai", 9.0)
    trend_db.add_trend_item("https://x/3", "Other beat", "src", "web-seo", 8.0)
    items = trend_db.top_items("local-ai")
    assert [i["id"] for i in items] == [hi, lo]
    assert set(trend_db.beats_with_pending_items()) == {"local-ai", "web-seo"}
    trend_db.mark_items_used([hi, lo])
    assert trend_db.top_items("local-ai") == []
    got = trend_db.get_items([hi])
    assert got[0]["title"] == "High item"


def test_source_fail_counters():
    trend_db.source_fail("deadfeed", "timeout")
    for _ in range(4):
        trend_db.source_fail("deadfeed", "timeout")
    assert trend_db.failing_sources(threshold=5) == [
        {"name": "deadfeed", "fail_count": 5}]
    trend_db.source_ok("deadfeed")
    assert trend_db.failing_sources(threshold=5) == []


def test_add_proposal_and_fts_dedup():
    pid = trend_db.add_proposal(
        beat="local-ai", agent_id="claw_batto",
        title="Adopt Qwen2.5 14B for receipt triage",
        rationale="New quant beats current model on OCR-adjacent tasks.",
        impact="Faster receipts", effort="M",
        suggested_assignee="claw_batto", cited_item_ids=[1, 2])
    assert pid is not None
    # near-identical title (>=60% token overlap) → duplicate
    dup = trend_db.add_proposal(
        beat="local-ai", agent_id="claw_batto",
        title="Adopt Qwen2.5 14B model for receipt triage now",
        rationale="Same idea.", impact="x", effort="S",
        suggested_assignee="claw_batto", cited_item_ids=[1])
    assert dup is None
    # unrelated title → accepted
    other = trend_db.add_proposal(
        beat="web-seo", agent_id="nova_sterling",
        title="Publish seasonal deck-maintenance landing page",
        rationale="Search interest spike.", impact="Leads", effort="S",
        suggested_assignee="nova_sterling", cited_item_ids=[3])
    assert other is not None and other != pid


def _mk_proposal(beat, title, n):
    return trend_db.add_proposal(
        beat=beat, agent_id="a", title=title,
        rationale=f"r{n}", impact="i", effort="S",
        suggested_assignee="scout_reeves", cited_item_ids=[n])


def test_digest_round_robin_across_beats():
    p1 = _mk_proposal("local-ai", "First ai idea alpha", 1)
    p2 = _mk_proposal("local-ai", "Second ai idea bravo", 2)
    p3 = _mk_proposal("web-seo", "Seo idea charlie", 3)
    picked = trend_db.proposals_for_digest(limit=3)
    beats = [p["beat"] for p in picked]
    # one per beat before a second from any beat
    assert set(beats[:2]) == {"local-ai", "web-seo"}
    assert {p["id"] for p in picked} == {p1, p2, p3}


def test_status_transitions_idempotent():
    pid = _mk_proposal("local-ai", "Transition test idea delta", 9)
    assert trend_db.set_status(pid, "digested") is True
    assert trend_db.set_status(pid, "approved", task_id="t-1") is True
    # second approve must fail (idempotency guard)
    assert trend_db.set_status(pid, "approved", task_id="t-2") is False
    p = trend_db.get_proposal(pid)
    assert p["status"] == "approved" and p["task_id"] == "t-1"
    # dismissed only from proposed/digested
    assert trend_db.set_status(pid, "dismissed") is False


def test_expire_stale():
    import sqlite3, os
    pid = _mk_proposal("local-ai", "Old idea echo foxtrot", 5)
    con = sqlite3.connect(os.environ["BAZA_TREND_DB"])
    con.execute("UPDATE idea_proposals SET created_at=datetime('now','-20 days') WHERE id=?", (pid,))
    con.commit(); con.close()
    assert trend_db.expire_stale(days=14) == 1
    assert trend_db.get_proposal(pid)["status"] == "expired"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_trend_db.py -v`
Expected: FAIL / ERROR with `ModuleNotFoundError: No module named 'core.trend_db'` (or ImportError).

- [ ] **Step 3: Write the implementation**

Create `core/trend_db.py`:

```python
"""
Baza Empire — Trend-Scout storage.
SQLite for the trend-research pipeline: source health, seen-URL dedup, scored
trend items, idea proposals. Separate DB from baza_projects.db (same pattern
as core/claw_review_db.py). Test override: BAZA_TREND_DB env var.
"""
import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

FRAMEWORK_DIR = Path(__file__).resolve().parent.parent

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
  name        TEXT PRIMARY KEY,
  fail_count  INTEGER NOT NULL DEFAULT 0,
  last_ok_at  TEXT,
  last_error  TEXT
);
CREATE TABLE IF NOT EXISTS seen_items (
  url_hash      TEXT PRIMARY KEY,
  url           TEXT NOT NULL,
  first_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trend_items (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  url           TEXT NOT NULL,
  title         TEXT NOT NULL,
  source        TEXT NOT NULL,
  beat          TEXT NOT NULL,
  score         REAL NOT NULL,
  summary       TEXT NOT NULL DEFAULT '',
  published_at  TEXT,
  scanned_at    TEXT NOT NULL,
  used_in_round INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_trend_items_beat ON trend_items(beat, used_in_round, score DESC);
CREATE TABLE IF NOT EXISTS idea_proposals (
  id                 TEXT PRIMARY KEY,
  beat               TEXT NOT NULL,
  agent_id           TEXT NOT NULL,
  title              TEXT NOT NULL,
  rationale          TEXT NOT NULL DEFAULT '',
  impact             TEXT NOT NULL DEFAULT '',
  effort             TEXT NOT NULL DEFAULT '',
  suggested_assignee TEXT NOT NULL,
  cited_item_ids     TEXT NOT NULL DEFAULT '[]',
  status             TEXT NOT NULL DEFAULT 'proposed'
      CHECK (status IN ('proposed','digested','approved','dismissed','expired')),
  task_id            TEXT,
  created_at         TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS proposals_fts
  USING fts5(proposal_id UNINDEXED, title, rationale);
"""

# allowed previous states for each status transition (atomic idempotency guard)
_TRANSITIONS = {
    "digested":  ("proposed",),
    "approved":  ("proposed", "digested"),
    "dismissed": ("proposed", "digested"),
    "expired":   ("proposed", "digested"),
}


def _db_path() -> Path:
    return Path(os.environ.get("BAZA_TREND_DB")
                or FRAMEWORK_DIR / "dashboard" / "trend_scout.db")


@contextmanager
def _conn():
    p = _db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), timeout=10.0)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def init_db() -> None:
    with _conn() as c:
        c.executescript(SCHEMA)


# ── seen items ────────────────────────────────────────────────────────────────

def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def is_seen(url: str) -> bool:
    with _conn() as c:
        row = c.execute("SELECT 1 FROM seen_items WHERE url_hash=?",
                        (url_hash(url),)).fetchone()
        return row is not None


def mark_seen(url: str) -> bool:
    """Insert URL into seen set. True if it was new."""
    with _conn() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO seen_items(url_hash, url, first_seen_at) VALUES (?,?,?)",
            (url_hash(url), url, _now()))
        return cur.rowcount == 1


# ── trend items ───────────────────────────────────────────────────────────────

def add_trend_item(url: str, title: str, source: str, beat: str, score: float,
                   summary: str = "", published_at: str = None) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO trend_items(url,title,source,beat,score,summary,published_at,scanned_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (url, title, source, beat, score, summary, published_at, _now()))
        return cur.lastrowid


def get_items(ids: list) -> list:
    if not ids:
        return []
    marks = ",".join("?" for _ in ids)
    with _conn() as c:
        rows = c.execute(f"SELECT * FROM trend_items WHERE id IN ({marks})",
                         list(ids)).fetchall()
        return [dict(r) for r in rows]


def top_items(beat: str, limit: int = 8) -> list:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM trend_items WHERE beat=? AND used_in_round=0"
            " ORDER BY score DESC, id DESC LIMIT ?", (beat, limit)).fetchall()
        return [dict(r) for r in rows]


def beats_with_pending_items() -> list:
    with _conn() as c:
        rows = c.execute(
            "SELECT DISTINCT beat FROM trend_items WHERE used_in_round=0").fetchall()
        return [r["beat"] for r in rows]


def mark_items_used(ids: list) -> None:
    if not ids:
        return
    marks = ",".join("?" for _ in ids)
    with _conn() as c:
        c.execute(f"UPDATE trend_items SET used_in_round=1 WHERE id IN ({marks})",
                  list(ids))


# ── source health ─────────────────────────────────────────────────────────────

def source_ok(name: str) -> None:
    with _conn() as c:
        c.execute("""
            INSERT INTO sources(name, fail_count, last_ok_at) VALUES (?, 0, ?)
            ON CONFLICT(name) DO UPDATE SET fail_count=0, last_ok_at=excluded.last_ok_at
        """, (name, _now()))


def source_fail(name: str, err: str) -> None:
    with _conn() as c:
        c.execute("""
            INSERT INTO sources(name, fail_count, last_error) VALUES (?, 1, ?)
            ON CONFLICT(name) DO UPDATE SET fail_count=fail_count+1, last_error=excluded.last_error
        """, (name, err[:300]))


def failing_sources(threshold: int = 5) -> list:
    with _conn() as c:
        rows = c.execute(
            "SELECT name, fail_count FROM sources WHERE fail_count>=? ORDER BY name",
            (threshold,)).fetchall()
        return [{"name": r["name"], "fail_count": r["fail_count"]} for r in rows]


# ── proposals ─────────────────────────────────────────────────────────────────

def _title_tokens(title: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (title or "").lower()) if len(w) > 2}


def _is_duplicate(title: str) -> bool:
    """FTS candidate lookup + Jaccard token overlap >= 0.6 on titles."""
    toks = _title_tokens(title)
    if not toks:
        return False
    query = " OR ".join(f'"{t}"' for t in sorted(toks))
    with _conn() as c:
        rows = c.execute(
            "SELECT title FROM proposals_fts WHERE proposals_fts MATCH ? LIMIT 20",
            (query,)).fetchall()
    for r in rows:
        other = _title_tokens(r["title"])
        if other and len(toks & other) / len(toks | other) >= 0.6:
            return True
    return False


def add_proposal(beat: str, agent_id: str, title: str, rationale: str,
                 impact: str, effort: str, suggested_assignee: str,
                 cited_item_ids: list) -> str:
    """Store a proposal. Returns id, or None when it duplicates an existing one."""
    if _is_duplicate(title):
        return None
    pid = hashlib.sha256(f"{beat}\x00{title}".encode()).hexdigest()[:16]
    with _conn() as c:
        cur = c.execute("""
            INSERT OR IGNORE INTO idea_proposals
              (id, beat, agent_id, title, rationale, impact, effort,
               suggested_assignee, cited_item_ids, status, created_at)
            VALUES (?,?,?,?,?,?,?,?,?, 'proposed', ?)
        """, (pid, beat, agent_id, title, rationale, impact, effort,
              suggested_assignee, json.dumps(list(cited_item_ids)), _now()))
        if cur.rowcount == 0:
            return None
        c.execute("INSERT INTO proposals_fts(proposal_id, title, rationale) VALUES (?,?,?)",
                  (pid, title, rationale))
    return pid


def get_proposal(pid: str):
    with _conn() as c:
        row = c.execute("SELECT * FROM idea_proposals WHERE id=?", (pid,)).fetchone()
        return dict(row) if row else None


def proposals_for_digest(limit: int = 5) -> list:
    """Round-robin across beats over status='proposed' (oldest first per beat)."""
    with _conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM idea_proposals WHERE status='proposed'"
            " ORDER BY created_at, rowid").fetchall()]
    by_beat = {}
    for r in rows:
        by_beat.setdefault(r["beat"], []).append(r)
    out = []
    while len(out) < limit and any(by_beat.values()):
        for beat in sorted(by_beat):
            if by_beat[beat] and len(out) < limit:
                out.append(by_beat[beat].pop(0))
    return out


def set_status(pid: str, status: str, task_id: str = None) -> bool:
    """Atomic guarded transition. False if current status doesn't allow it."""
    prev = _TRANSITIONS.get(status, ())
    if not prev:
        return False
    marks = ",".join("?" for _ in prev)
    with _conn() as c:
        cur = c.execute(
            f"UPDATE idea_proposals SET status=?, task_id=COALESCE(?, task_id)"
            f" WHERE id=? AND status IN ({marks})",
            (status, task_id, pid, *prev))
        return cur.rowcount == 1


def set_task_id(pid: str, task_id: str) -> None:
    with _conn() as c:
        c.execute("UPDATE idea_proposals SET task_id=? WHERE id=?", (task_id, pid))


def expire_stale(days: int = 14) -> int:
    with _conn() as c:
        cur = c.execute(
            "UPDATE idea_proposals SET status='expired'"
            " WHERE status IN ('proposed','digested')"
            " AND created_at < datetime('now', ?)", (f"-{int(days)} days",))
        return cur.rowcount
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_trend_db.py -v`
Expected: all 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add core/trend_db.py tests/test_trend_db.py
git commit -m "feat(trends): trend_scout.db storage module (items, sources, proposals + FTS dedup)"
```

---

### Task 2: Source registry + fetchers (`config/trend_sources.yaml`, `core/trend_sources.py`)

**Files:**
- Create: `config/trend_sources.yaml`
- Create: `core/trend_sources.py`
- Modify: `requirements.txt` (append `feedparser>=6.0`)
- Test: `tests/test_trend_sources.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces (used by Task 4):
  - `load_sources(path=None) -> list[dict]` — enabled, validated entries `{name, type, url, beats}`; path from arg, `BAZA_TREND_SOURCES` env, or `config/trend_sources.yaml`.
  - `fetch_source(src: dict, timeout: int = 15) -> list[dict]` — normalized items `{url, title, published_at, snippet}`; **raises** on network/HTTP errors (caller isolates).
  - `VALID_TYPES = {"rss", "hn", "reddit_json"}`

- [ ] **Step 1: Install the dependency**

```bash
venv/bin/pip install "feedparser>=6.0"
echo "feedparser>=6.0" >> requirements.txt
```

Run: `venv/bin/python -c "import feedparser; print(feedparser.__version__)"`
Expected: a version like `6.x.x`.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_trend_sources.py`:

```python
import pytest
from core import trend_sources

RSS_XML = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Feed</title>
<item><title>Local model beats cloud</title>
  <link>https://example.com/post1</link>
  <pubDate>Mon, 30 Jun 2026 10:00:00 GMT</pubDate>
  <description>A new 14B quant outperforms.</description></item>
<item><title></title><link>https://example.com/notitle</link></item>
</channel></rss>"""

HN_JSON = {"hits": [
    {"title": "Show HN: agent bus", "url": "https://example.com/hn1",
     "created_at": "2026-06-30T09:00:00Z", "story_text": None, "objectID": "1"},
    {"title": "Ask HN: no url", "url": None,
     "created_at": "2026-06-30T08:00:00Z", "story_text": "body", "objectID": "42"},
]}

REDDIT_JSON = {"data": {"children": [
    {"data": {"title": "New GGUF drop", "permalink": "/r/LocalLLaMA/abc",
              "created_utc": 1782800000, "selftext": "quant details", "stickied": False}},
    {"data": {"title": "Sticky rules", "permalink": "/r/LocalLLaMA/rules",
              "created_utc": 1782800000, "selftext": "", "stickied": True}},
]}}


def test_parse_rss_skips_titleless():
    items = trend_sources._parse_rss(RSS_XML)
    assert len(items) == 1
    it = items[0]
    assert it["url"] == "https://example.com/post1"
    assert it["title"] == "Local model beats cloud"
    assert it["published_at"].startswith("2026-06-30")
    assert "14B quant" in it["snippet"]


def test_parse_hn_falls_back_to_item_page():
    items = trend_sources._parse_hn(HN_JSON)
    assert items[0]["url"] == "https://example.com/hn1"
    assert items[1]["url"] == "https://news.ycombinator.com/item?id=42"
    assert items[0]["published_at"] == "2026-06-30 09:00:00"


def test_parse_reddit_skips_stickies():
    items = trend_sources._parse_reddit(REDDIT_JSON)
    assert len(items) == 1
    assert items[0]["url"] == "https://www.reddit.com/r/LocalLLaMA/abc"
    assert items[0]["title"] == "New GGUF drop"


def test_load_sources_validates(tmp_path, monkeypatch):
    cfg = tmp_path / "sources.yaml"
    cfg.write_text("""
sources:
  - name: good
    type: rss
    url: https://example.com/feed
    beats: [local-ai]
  - name: disabled
    type: rss
    url: https://example.com/feed2
    beats: [local-ai]
    enabled: false
  - name: badtype
    type: carrier_pigeon
    url: https://example.com/x
    beats: [local-ai]
  - name: missing-beats
    type: rss
    url: https://example.com/y
""")
    monkeypatch.setenv("BAZA_TREND_SOURCES", str(cfg))
    srcs = trend_sources.load_sources()
    assert [s["name"] for s in srcs] == ["good"]


def test_fetch_source_dispatch(monkeypatch):
    class Resp:
        content = RSS_XML
        def raise_for_status(self): pass
        def json(self): return HN_JSON
    monkeypatch.setattr(trend_sources.requests, "get", lambda *a, **k: Resp())
    rss_items = trend_sources.fetch_source(
        {"name": "r", "type": "rss", "url": "https://x/feed", "beats": ["local-ai"]})
    assert rss_items[0]["title"] == "Local model beats cloud"
    hn_items = trend_sources.fetch_source(
        {"name": "h", "type": "hn", "url": "https://x/hn", "beats": ["agent-tech"]})
    assert hn_items[0]["title"] == "Show HN: agent bus"


def test_fetch_source_raises_on_http_error(monkeypatch):
    class Boom:
        def raise_for_status(self): raise RuntimeError("500")
    monkeypatch.setattr(trend_sources.requests, "get", lambda *a, **k: Boom())
    with pytest.raises(RuntimeError):
        trend_sources.fetch_source(
            {"name": "d", "type": "rss", "url": "https://x/dead", "beats": ["local-ai"]})


def test_default_registry_loads_and_is_valid():
    srcs = trend_sources.load_sources()   # real config/trend_sources.yaml
    assert len(srcs) >= 10
    for s in srcs:
        assert s["type"] in trend_sources.VALID_TYPES
        assert s["beats"], f"{s['name']} has no beats"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_trend_sources.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.trend_sources'`.

- [ ] **Step 4: Write the seed registry**

Create `config/trend_sources.yaml`:

```yaml
# Trend-Scout source registry.
# type ∈ {rss, hn, reddit_json}. Adding a source = one entry here, no code.
# Beats: local-ai, agent-tech, remodeling-market, materials-pricing, marketing-seo, web-seo
sources:
  # ── AI / tech for the empire ──────────────────────────────────────────────
  - name: r-localllama
    type: reddit_json
    url: https://www.reddit.com/r/LocalLLaMA/hot.json?limit=25
    beats: [local-ai]
  - name: hn-local-llm
    type: hn
    url: "https://hn.algolia.com/api/v1/search_by_date?tags=story&query=ollama%20OR%20llama.cpp&numericFilters=points>10"
    beats: [local-ai]
  - name: hn-llm-agents
    type: hn
    url: "https://hn.algolia.com/api/v1/search_by_date?tags=story&query=LLM%20agents&numericFilters=points>20"
    beats: [agent-tech]
  - name: ollama-releases
    type: rss
    url: https://github.com/ollama/ollama/releases.atom
    beats: [local-ai]
  - name: huggingface-blog
    type: rss
    url: https://huggingface.co/blog/feed.xml
    beats: [local-ai, agent-tech]
  - name: simon-willison
    type: rss
    url: https://simonwillison.net/atom/everything/
    beats: [agent-tech]
  # ── AHBCO business ────────────────────────────────────────────────────────
  - name: construction-dive
    type: rss
    url: https://www.constructiondive.com/feeds/news/
    beats: [remodeling-market, materials-pricing]
  - name: nahb-now
    type: rss
    url: https://nahbnow.com/feed/
    beats: [remodeling-market]
  - name: jlc-online
    type: rss
    url: https://www.jlconline.com/feed/
    beats: [remodeling-market]
  - name: r-smallbusiness
    type: reddit_json
    url: https://www.reddit.com/r/smallbusiness/hot.json?limit=25
    beats: [marketing-seo]
  - name: search-engine-land
    type: rss
    url: https://searchengineland.com/feed
    beats: [web-seo]
  - name: moz-blog
    type: rss
    url: https://moz.com/posts/rss/blog
    beats: [marketing-seo, web-seo]
```

Then verify every URL is actually reachable and parses (feeds move; replace any dead one with an equivalent for the same beat before proceeding):

```bash
venv/bin/python - <<'EOF'
from core.trend_sources import load_sources, fetch_source
for s in load_sources():
    try:
        items = fetch_source(s)
        print(f"OK   {s['name']:20} {len(items)} items")
    except Exception as e:
        print(f"DEAD {s['name']:20} {e}")
EOF
```

(This script needs `core/trend_sources.py` from Step 5 — run it after Step 5 if you prefer; the requirement is that by the end of this task every registry entry prints `OK` with ≥1 item, or is replaced/removed.)

- [ ] **Step 5: Write the implementation**

Create `core/trend_sources.py`:

```python
"""
Baza Empire — trend source registry + fetchers.
Declarative sources in config/trend_sources.yaml; each fetch returns
normalized items: {url, title, published_at, snippet}.
Fetchers RAISE on failure — the scanner isolates per-source errors.
"""
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import requests
import yaml

logger = logging.getLogger("baza.trend_sources")

FRAMEWORK_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = FRAMEWORK_DIR / "config" / "trend_sources.yaml"
VALID_TYPES = {"rss", "hn", "reddit_json"}
HEADERS = {"User-Agent": "baza-trend-scout/1.0 (+https://ahb123.com)"}
MAX_ITEMS_PER_SOURCE = 30


def load_sources(path=None) -> list:
    p = Path(path or os.environ.get("BAZA_TREND_SOURCES") or DEFAULT_CONFIG)
    data = yaml.safe_load(p.read_text()) or {}
    out = []
    for s in data.get("sources", []):
        if not s.get("enabled", True):
            continue
        missing = {"name", "type", "url", "beats"} - set(s)
        if missing or s["type"] not in VALID_TYPES or not s.get("beats"):
            logger.warning(f"skipping invalid source entry: {s.get('name', s)}")
            continue
        out.append(s)
    return out


def _parse_rss(content: bytes) -> list:
    feed = feedparser.parse(content)
    items = []
    for e in feed.entries[:MAX_ITEMS_PER_SOURCE]:
        url = (getattr(e, "link", "") or "").strip()
        title = (getattr(e, "title", "") or "").strip()
        if not url or not title:
            continue
        published = ""
        if getattr(e, "published_parsed", None):
            published = time.strftime("%Y-%m-%d %H:%M:%S", e.published_parsed)
        snippet = (getattr(e, "summary", "") or "")[:300]
        items.append({"url": url, "title": title,
                      "published_at": published, "snippet": snippet})
    return items


def _parse_hn(payload: dict) -> list:
    items = []
    for h in (payload.get("hits") or [])[:MAX_ITEMS_PER_SOURCE]:
        title = (h.get("title") or "").strip()
        if not title:
            continue
        url = h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID', '')}"
        published = (h.get("created_at") or "")[:19].replace("T", " ")
        items.append({"url": url, "title": title, "published_at": published,
                      "snippet": (h.get("story_text") or "")[:300]})
    return items


def _parse_reddit(payload: dict) -> list:
    items = []
    for child in (payload.get("data", {}).get("children") or [])[:MAX_ITEMS_PER_SOURCE]:
        d = child.get("data", {})
        title = (d.get("title") or "").strip()
        if not title or d.get("stickied"):
            continue
        url = "https://www.reddit.com" + (d.get("permalink") or "")
        published = ""
        if d.get("created_utc"):
            published = datetime.fromtimestamp(
                d["created_utc"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        items.append({"url": url, "title": title, "published_at": published,
                      "snippet": (d.get("selftext") or "")[:300]})
    return items


def fetch_source(src: dict, timeout: int = 15) -> list:
    """Fetch one registry entry → normalized items. Raises on failure."""
    resp = requests.get(src["url"], headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    t = src["type"]
    if t == "rss":
        return _parse_rss(resp.content)
    if t == "hn":
        return _parse_hn(resp.json())
    if t == "reddit_json":
        return _parse_reddit(resp.json())
    raise ValueError(f"unknown source type: {t}")
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_trend_sources.py -v`
Expected: all 7 tests PASS. (`test_default_registry_loads_and_is_valid` only reads YAML — no network.)

- [ ] **Step 7: Verify live feeds (the Step 4 script) and fix any DEAD entries**

Run the Step 4 verification script. Replace or delete dead sources until every line prints `OK ... ≥1 items`. Re-run `venv/bin/pytest tests/test_trend_sources.py -v` after edits.

- [ ] **Step 8: Commit**

```bash
git add core/trend_sources.py config/trend_sources.yaml tests/test_trend_sources.py requirements.txt
git commit -m "feat(trends): source registry (yaml) + rss/hn/reddit fetchers"
```

---

### Task 3: Event-name constants + new channels + BaseAgent subscription

**Files:**
- Create: `core/event_names.py`
- Modify: `core/event_bus.py:44-56` (CHANNELS dict)
- Modify: `core/base_agent.py:1795-1800` (`start_event_listener` listen tuple)
- Test: `tests/test_event_names.py`

**Interfaces:**
- Produces (used by Tasks 4–7):
  - `core.event_names.EV_TREND_SCAN_COMPLETE = "trend_scan_complete"`
  - `core.event_names.EV_IDEA_PROPOSED = "idea_proposed"`
  - `core.event_names.EV_IDEA_APPROVED = "idea_approved"`
  - plus constants for all 11 existing channels (`EV_RESEARCH_COMPLETE`, `EV_TASK_CREATED`, …).

- [ ] **Step 1: Write the failing test**

Create `tests/test_event_names.py`:

```python
from core import event_names
from core.event_bus import CHANNELS


def test_every_constant_has_a_channel():
    consts = {v for k, v in vars(event_names).items() if k.startswith("EV_")}
    assert consts, "no EV_ constants found"
    for name in consts:
        assert name in CHANNELS, f"{name} missing from event_bus.CHANNELS"


def test_trend_events_registered():
    assert event_names.EV_TREND_SCAN_COMPLETE == "trend_scan_complete"
    assert CHANNELS["trend_scan_complete"] == "baza:events:trend_scan_complete"
    assert CHANNELS["idea_proposed"] == "baza:events:idea_proposed"
    assert CHANNELS["idea_approved"] == "baza:events:idea_approved"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/test_event_names.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.event_names'`.

- [ ] **Step 3: Implement**

Create `core/event_names.py`:

```python
"""
Canonical event channel names. Import these instead of ad-hoc strings so new
event types can't silently drift. Payload shape documented per constant.
Every EV_* constant MUST also appear in core.event_bus.CHANNELS (test-enforced).
"""
EV_RESEARCH_COMPLETE   = "research_complete"    # {topic, artifact, summary}
EV_TASK_CREATED        = "task_created"         # {task_id, title, assigned_to, description}
EV_TASK_COMPLETED      = "task_completed"       # {task_id, agent_id}
EV_TASK_BLOCKED        = "task_blocked"         # {task_id, agent_id, reason}
EV_KNOWLEDGE_UPDATED   = "knowledge_updated"    # {key, category, updated_by}
EV_CONTEXT_INVALIDATED = "context_invalidated"  # {agent_id}
EV_DISPATCH            = "dispatch"             # {target, instruction}
EV_AGENT_ALERT         = "agent_alert"          # {target, message, ...}
EV_AGENT_HELP_REQUEST  = "agent_help_request"   # {agent_id, question}
EV_AGENT_HELP_RESPONSE = "agent_help_response"  # {agent_id, answer}
EV_REPORT_GENERATED    = "report_generated"     # {agent_id, report}
# ── trend pipeline (2026-07) ──────────────────────────────────────────────────
EV_TREND_SCAN_COMPLETE = "trend_scan_complete"  # {sources_ok, sources_failed, new_items, stored, knowledge}
EV_IDEA_PROPOSED       = "idea_proposed"        # {proposal_id, beat, title, suggested_assignee}
EV_IDEA_APPROVED       = "idea_approved"        # {proposal_id, task_id, title, assignee}
```

In `core/event_bus.py`, extend the `CHANNELS` dict (after the `"report_generated"` line, before the closing brace):

```python
    "trend_scan_complete": "baza:events:trend_scan_complete",
    "idea_proposed": "baza:events:idea_proposed",
    "idea_approved": "baza:events:idea_approved",
```

In `core/base_agent.py` `start_event_listener` (line ~1800), change the listen call from:

```python
            async for event in self.event_bus.listen("research_complete", "agent_alert", "knowledge_updated"):
```

to:

```python
            async for event in self.event_bus.listen(
                    "research_complete", "agent_alert", "knowledge_updated",
                    "trend_scan_complete", "idea_proposed", "idea_approved"):
```

(The default `_handle_event` logs the event; agents can override — that satisfies "the bus finally has consumers" without behavior risk.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_event_names.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add core/event_names.py core/event_bus.py core/base_agent.py tests/test_event_names.py
git commit -m "feat(events): canonical event-name constants + trend channels + BaseAgent subscribes"
```

---

### Task 4: Scanner (`core/trend_scout.py`) + systemd units

**Files:**
- Create: `core/trend_scout.py`
- Create: `systemd/baza-trend-scout.service`
- Create: `systemd/baza-trend-scout.timer`
- Test: `tests/test_trend_scout.py`

**Interfaces:**
- Consumes: `trend_db` (Task 1), `trend_sources.load_sources/fetch_source` (Task 2), `event_names.EV_TREND_SCAN_COMPLETE` (Task 3), `core.context_db.empire_set`, `core.event_bus.publish_sync`.
- Produces (used by Task 12 deploy):
  - `run_scan(sources=None, fetch=None, llm=None) -> dict` counts.
  - `score_batch(items, beats, llm=None) -> list | None` — `None` = LLM failure (items stay unseen, retried next scan); `[]` = scored, nothing relevant.
  - CLI: `venv/bin/python core/trend_scout.py [--dry-run]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_trend_scout.py`:

```python
import json
import pytest
from core import trend_db, trend_scout


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("BAZA_TREND_DB", str(tmp_path / "trend.db"))
    trend_db.init_db()


@pytest.fixture(autouse=True)
def no_side_effects(monkeypatch):
    monkeypatch.setattr(trend_scout, "publish_sync", lambda *a, **k: None)
    monkeypatch.setattr(trend_scout, "empire_set", lambda **k: None)


SRC = {"name": "feed-a", "type": "rss", "url": "https://x/a", "beats": ["local-ai"]}
ITEMS = [
    {"url": "https://x/hot", "title": "Hot new 14B model", "published_at": "", "snippet": "big"},
    {"url": "https://x/meh", "title": "Mild release note", "published_at": "", "snippet": "small"},
]


def _llm_scoring(prompt):
    return json.dumps({"items": [
        {"n": 1, "score": 9, "beat": "local-ai", "summary": "hot"},
        {"n": 2, "score": 3, "beat": "local-ai", "summary": "meh"},
    ]})


def test_scan_stores_above_threshold_and_marks_seen():
    counts = trend_scout.run_scan(
        sources=[SRC], fetch=lambda s, timeout=15: list(ITEMS), llm=_llm_scoring)
    assert counts["sources_ok"] == 1
    assert counts["new_items"] == 2
    assert counts["stored"] == 1          # only score 9 >= 6
    stored = trend_db.top_items("local-ai")
    assert len(stored) == 1 and stored[0]["title"] == "Hot new 14B model"
    assert trend_db.is_seen("https://x/meh")   # low score still consumed


def test_scan_is_idempotent_on_seen_urls():
    trend_scout.run_scan(sources=[SRC], fetch=lambda s, timeout=15: list(ITEMS), llm=_llm_scoring)
    counts = trend_scout.run_scan(sources=[SRC], fetch=lambda s, timeout=15: list(ITEMS), llm=_llm_scoring)
    assert counts["new_items"] == 0 and counts["stored"] == 0


def test_source_failure_is_isolated():
    def fetch(s, timeout=15):
        if s["name"] == "dead":
            raise RuntimeError("boom")
        return list(ITEMS)
    dead = {"name": "dead", "type": "rss", "url": "https://x/d", "beats": ["local-ai"]}
    counts = trend_scout.run_scan(sources=[dead, SRC], fetch=fetch, llm=_llm_scoring)
    assert counts["sources_failed"] == 1 and counts["sources_ok"] == 1
    assert counts["stored"] == 1
    assert trend_db.failing_sources(threshold=1) == [{"name": "dead", "fail_count": 1}]


def test_llm_failure_leaves_items_unseen_for_retry():
    counts = trend_scout.run_scan(
        sources=[SRC], fetch=lambda s, timeout=15: list(ITEMS),
        llm=lambda p: "NOT JSON {{{")
    assert counts["stored"] == 0
    assert not trend_db.is_seen("https://x/hot")   # retried next scan
    # next scan with a working LLM picks them up
    counts2 = trend_scout.run_scan(
        sources=[SRC], fetch=lambda s, timeout=15: list(ITEMS), llm=_llm_scoring)
    assert counts2["stored"] == 1


def test_high_score_publishes_to_empire_knowledge(monkeypatch):
    calls = []
    monkeypatch.setattr(trend_scout, "empire_set", lambda **k: calls.append(k))
    trend_scout.run_scan(sources=[SRC], fetch=lambda s, timeout=15: list(ITEMS), llm=_llm_scoring)
    assert len(calls) == 1
    assert calls[0]["category"] == "trends"
    assert "Hot new 14B model" in calls[0]["value"]


def test_score_batch_clamps_and_validates():
    raw = json.dumps({"items": [
        {"n": 1, "score": 99, "beat": "local-ai", "summary": "s"},
        {"n": 7, "score": 5, "beat": "local-ai", "summary": "bad n"},
        {"n": 2, "score": 5, "beat": "not-a-beat", "summary": "bad beat"},
    ]})
    rows = trend_scout.score_batch(ITEMS, ["local-ai"], llm=lambda p: raw)
    assert rows == [{"n": 1, "score": 10.0, "beat": "local-ai", "summary": "s"}]


def test_score_batch_returns_none_on_llm_error():
    def boom(p): raise RuntimeError("ollama down")
    assert trend_scout.score_batch(ITEMS, ["local-ai"], llm=boom) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_trend_scout.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.trend_scout'`.

- [ ] **Step 3: Implement**

Create `core/trend_scout.py`:

```python
"""
Baza Empire — Trend-Scout scanner. Runs every 4h via baza-trend-scout.timer.
fetch sources → dedupe seen URLs → local-Ollama relevance scoring →
trend_items (score>=6) + empire_knowledge (score>=8) → trend_scan_complete event.
LOCAL-FIRST: scoring model is local Ollama only.
"""
import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from core import trend_db
from core.trend_sources import load_sources, fetch_source
from core.event_names import EV_TREND_SCAN_COMPLETE
from core.event_bus import publish_sync
from core.context_db import empire_set

logger = logging.getLogger("baza.trend_scout")

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
SCORING_MODEL = os.getenv("TREND_SCORING_MODEL", "qwen2.5:14b")
STORE_THRESHOLD = 6.0
KNOWLEDGE_THRESHOLD = 8.0
BATCH = 10

BEAT_DESCRIPTIONS = {
    "local-ai": "new local/open LLM models, quantization, Ollama/llama.cpp ecosystem",
    "agent-tech": "agent frameworks, orchestration, tool use, MCP, memory systems",
    "remodeling-market": "residential remodeling/construction market news relevant to a PA contractor",
    "materials-pricing": "building-materials pricing and supply news (lumber, drywall, fixtures)",
    "marketing-seo": "small-business marketing tactics, social, lead generation",
    "web-seo": "SEO / local-search changes relevant to a contractor website",
}


def _default_llm(prompt: str) -> str:
    resp = requests.post(f"{OLLAMA_URL}/api/chat", json={
        "model": SCORING_MODEL, "stream": False, "format": "json",
        "options": {"temperature": 0.1, "num_predict": 1500},
        "messages": [{"role": "user", "content": prompt}],
    }, timeout=180)
    resp.raise_for_status()
    return resp.json()["message"]["content"]


def score_batch(items: list, beats: list, llm=None):
    """Score up to BATCH items against beats.
    Returns list of {n, score, beat, summary} (validated, clamped),
    or None when the LLM call/parse failed (caller leaves items unseen)."""
    llm = llm or _default_llm
    beat_lines = "\n".join(f"- {b}: {BEAT_DESCRIPTIONS.get(b, b)}" for b in beats)
    item_lines = "\n".join(
        f"{i + 1}. {it['title']} — {(it.get('snippet') or '')[:200]}"
        for i, it in enumerate(items))
    prompt = (
        "You score news items for relevance to specific beats.\n"
        f"Beats:\n{beat_lines}\n\nItems:\n{item_lines}\n\n"
        'Return ONLY JSON: {"items":[{"n":<item number>,"score":<0-10>,'
        '"beat":"<one beat from the list>","summary":"<one line, max 25 words>"}]}\n'
        "Score 8-10 = act on this now; 6-7 = worth knowing; below 6 = noise."
    )
    try:
        data = json.loads(llm(prompt))
        out = []
        for row in data.get("items", []):
            try:
                n = int(row.get("n", 0))
                score = float(row.get("score", 0))
            except (TypeError, ValueError):
                continue
            beat = row.get("beat", "")
            if 1 <= n <= len(items) and beat in beats:
                out.append({"n": n, "score": max(0.0, min(10.0, score)),
                            "beat": beat,
                            "summary": str(row.get("summary", ""))[:200]})
        return out
    except Exception as e:
        logger.warning(f"score_batch: unusable LLM output ({e})")
        return None


def run_scan(sources=None, fetch=None, llm=None) -> dict:
    trend_db.init_db()
    fetch = fetch or fetch_source
    sources = load_sources() if sources is None else sources
    counts = {"sources_ok": 0, "sources_failed": 0,
              "new_items": 0, "stored": 0, "knowledge": 0}
    for src in sources:
        try:
            raw = fetch(src)
            trend_db.source_ok(src["name"])
            counts["sources_ok"] += 1
        except Exception as e:
            trend_db.source_fail(src["name"], str(e))
            counts["sources_failed"] += 1
            logger.warning(f"source {src['name']} failed: {e}")
            continue
        fresh = [it for it in raw if not trend_db.is_seen(it["url"])]
        counts["new_items"] += len(fresh)
        for i in range(0, len(fresh), BATCH):
            batch = fresh[i:i + BATCH]
            rows = score_batch(batch, src["beats"], llm=llm)
            if rows is None:
                continue  # LLM failed — leave batch unseen, retry next scan
            for it in batch:
                trend_db.mark_seen(it["url"])
            for row in rows:
                if row["score"] < STORE_THRESHOLD:
                    continue
                it = batch[row["n"] - 1]
                trend_db.add_trend_item(
                    url=it["url"], title=it["title"], source=src["name"],
                    beat=row["beat"], score=row["score"], summary=row["summary"],
                    published_at=it.get("published_at"))
                counts["stored"] += 1
                if row["score"] >= KNOWLEDGE_THRESHOLD:
                    try:
                        empire_set(
                            key=f"trend_{trend_db.url_hash(it['url'])[:12]}",
                            value=f"[{row['beat']}] {it['title']} — {row['summary']} ({it['url']})",
                            category="trends", updated_by="trend_scout")
                        counts["knowledge"] += 1
                    except Exception as e:
                        logger.warning(f"empire_set failed (non-fatal): {e}")
    publish_sync("trend_scout", EV_TREND_SCAN_COMPLETE, counts)
    logger.info(f"scan complete: {counts}")
    return counts


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Baza trend scanner")
    ap.add_argument("--dry-run", action="store_true",
                    help="list enabled sources, no fetch/score")
    args = ap.parse_args()
    if args.dry_run:
        for s in load_sources():
            print(f"{s['name']:22} [{s['type']}] beats={s['beats']}")
        return
    run_scan()


if __name__ == "__main__":
    main()
```

Note: `test_high_score_publishes_to_empire_knowledge` monkeypatches `trend_scout.empire_set` — the module-level import makes that work. Never let a PostgreSQL outage kill the scan (hence the try/except around `empire_set`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_trend_scout.py -v`
Expected: all 7 tests PASS.

- [ ] **Step 5: Write the systemd units** (repo copies; installed in Task 12)

Create `systemd/baza-trend-scout.service`:

```ini
[Unit]
Description=Baza Empire — Trend-Scout scanner (fetch + score sources)
After=network.target ollama.service

[Service]
Type=oneshot
User=switchhacker
WorkingDirectory=/home/switchhacker/baza-empire/agent-framework-v3
EnvironmentFile=/home/switchhacker/baza-empire/agent-framework-v3/configs/secrets.env
ExecStart=/home/switchhacker/baza-empire/agent-framework-v3/venv/bin/python core/trend_scout.py
StandardOutput=journal
StandardError=journal
SyslogIdentifier=baza-trend-scout
```

Create `systemd/baza-trend-scout.timer`:

```ini
[Unit]
Description=Run the Baza trend scanner every 4 hours

[Timer]
OnCalendar=*-*-* 00/4:15:00
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
```

- [ ] **Step 6: Commit**

```bash
git add core/trend_scout.py tests/test_trend_scout.py systemd/baza-trend-scout.service systemd/baza-trend-scout.timer
git commit -m "feat(trends): 4-hourly scanner — fetch, dedupe, local-LLM scoring, knowledge publish"
```

---

### Task 5: Idea engine (`core/idea_engine.py`)

**Files:**
- Create: `core/idea_engine.py`
- Test: `tests/test_idea_engine.py`

**Interfaces:**
- Consumes: `trend_db` (Task 1), `event_names.EV_IDEA_PROPOSED` (Task 3), personas at `agents/<id>/persona/{IDENTITY,SOUL,MISSION}.md`.
- Produces (used by Task 6):
  - `run_idea_round(llm=None) -> list[str]` — created proposal ids. `llm(system: str, prompt: str) -> str`.
  - `BEAT_OWNERS: dict[str, str]`, `DEFAULT_OWNER = "scout_reeves"`, `VALID_ASSIGNEES: set[str]`
  - `parse_proposals(raw: str, valid_item_ids: set) -> list[dict]` with keys `title, rationale, impact, effort, assignee, cites`.
  - CLI: `venv/bin/python core/idea_engine.py [--no-digest]` (digest wiring added in Task 6).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_idea_engine.py`:

```python
import json
import pytest
from core import trend_db, idea_engine


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("BAZA_TREND_DB", str(tmp_path / "trend.db"))
    trend_db.init_db()
    monkeypatch.setattr(idea_engine, "publish_sync", lambda *a, **k: None)


def _seed_items(beat="local-ai", n=2):
    return [trend_db.add_trend_item(f"https://x/{beat}/{i}", f"{beat} item {i}",
                                    "src", beat, 8.0 - i, summary=f"sum {i}")
            for i in range(n)]


def _llm_factory(item_ids, assignee="claw_batto", title="Try the new quant pipeline"):
    def llm(system, prompt):
        return json.dumps({"proposals": [{
            "title": title,
            "rationale": "Items show a clear win.",
            "impact": "Faster local inference",
            "effort": "M",
            "suggested_assignee": assignee,
            "cites": item_ids,
        }]})
    return llm


def test_round_creates_proposals_and_marks_items_used():
    ids = _seed_items()
    created = idea_engine.run_idea_round(llm=_llm_factory(ids))
    assert len(created) == 1
    p = trend_db.get_proposal(created[0])
    assert p["beat"] == "local-ai"
    assert p["agent_id"] == idea_engine.BEAT_OWNERS["local-ai"]
    assert json.loads(p["cited_item_ids"]) == ids
    assert trend_db.top_items("local-ai") == []      # consumed


def test_proposal_without_valid_citation_rejected():
    ids = _seed_items()
    created = idea_engine.run_idea_round(llm=_llm_factory([999]))  # bogus cite
    assert created == []
    # items still consumed for this round (the round ran; the LLM just produced junk)
    assert trend_db.top_items("local-ai") == []


def test_invalid_assignee_falls_back_to_beat_owner():
    ids = _seed_items()
    created = idea_engine.run_idea_round(llm=_llm_factory(ids, assignee="elon_musk"))
    p = trend_db.get_proposal(created[0])
    assert p["suggested_assignee"] == idea_engine.BEAT_OWNERS["local-ai"]


def test_llm_failure_leaves_items_for_tomorrow():
    _seed_items()
    def boom(system, prompt): raise RuntimeError("ollama down")
    created = idea_engine.run_idea_round(llm=boom)
    assert created == []
    assert len(trend_db.top_items("local-ai")) == 2  # retried next round


def test_parse_proposals_handles_garbage():
    assert idea_engine.parse_proposals("not json", {1}) == []
    assert idea_engine.parse_proposals(json.dumps({"proposals": "nope"}), {1}) == []


def test_load_persona_reads_md_files():
    text = idea_engine.load_persona("scout_reeves")
    assert isinstance(text, str) and len(text) > 100  # real persona files exist
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_idea_engine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.idea_engine'`.

- [ ] **Step 3: Implement**

Create `core/idea_engine.py`:

```python
"""
Baza Empire — daily idea round. For each beat with fresh trend items, run the
owning agent's persona (local Ollama) over the top items and store 0-3 concrete
proposals. Digest sending lives in core/trend_digest.py (wired in main()).
LOCAL-FIRST: idea model is local Ollama only.
"""
import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from core import trend_db
from core.event_bus import publish_sync
from core.event_names import EV_IDEA_PROPOSED

logger = logging.getLogger("baza.idea_engine")

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
IDEA_MODEL = os.getenv("TREND_IDEA_MODEL", "gpt-oss:20b")

BEAT_OWNERS = {
    "local-ai": "claw_batto",
    "agent-tech": "claw_batto",
    "remodeling-market": "phil_hass",
    "materials-pricing": "phil_hass",
    "marketing-seo": "duke_harmon",
    "web-seo": "nova_sterling",
}
DEFAULT_OWNER = "scout_reeves"
VALID_ASSIGNEES = {
    "simon_bately", "claw_batto", "phil_hass", "sam_axe", "rex_valor",
    "duke_harmon", "scout_reeves", "nova_sterling", "specter_voss",
}
MAX_ITEMS_PER_BEAT = 8
MAX_PROPOSALS_PER_BEAT = 3


def load_persona(agent_id: str, max_chars: int = 4000) -> str:
    parts = []
    base = os.path.join(FRAMEWORK_DIR, "agents", agent_id, "persona")
    for fn in ("IDENTITY.md", "SOUL.md", "MISSION.md"):
        p = os.path.join(base, fn)
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    parts.append(f.read())
            except OSError:
                pass
    return "\n\n".join(parts)[:max_chars]


def build_prompt(beat: str, items: list) -> str:
    lines = "\n".join(
        f"[{it['id']}] (score {it['score']}) {it['title']} — {it['summary']} {it['url']}"
        for it in items)
    return (
        f"Fresh trend items on your beat '{beat}':\n{lines}\n\n"
        "Propose 0-3 concrete, actionable ideas for Serge's business "
        "(All Home Building Co) and its local-first agent platform (baza). "
        "Only propose an idea the items genuinely support — zero proposals is fine.\n"
        'Return ONLY JSON: {"proposals":[{"title":"<max 90 chars>",'
        '"rationale":"<why now, 2-3 sentences>","impact":"<benefit, 1 sentence>",'
        '"effort":"S|M|L","suggested_assignee":"<agent id>",'
        '"cites":[<item ids from the list above>]}]}\n'
        f"Valid assignees: {sorted(VALID_ASSIGNEES)}.\n"
        "Every proposal MUST cite at least one item id."
    )


def _default_llm(system: str, prompt: str) -> str:
    resp = requests.post(f"{OLLAMA_URL}/api/chat", json={
        "model": IDEA_MODEL, "stream": False, "format": "json",
        "options": {"temperature": 0.4, "num_predict": 1800},
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": prompt}],
    }, timeout=300)
    resp.raise_for_status()
    return resp.json()["message"]["content"]


def parse_proposals(raw: str, valid_item_ids: set) -> list:
    try:
        data = json.loads(raw)
        candidates = data.get("proposals")
        if not isinstance(candidates, list):
            return []
    except Exception:
        return []
    out = []
    for p in candidates[:MAX_PROPOSALS_PER_BEAT]:
        if not isinstance(p, dict):
            continue
        title = str(p.get("title", "")).strip()[:90]
        cites = []
        for c in (p.get("cites") or []):
            try:
                c = int(c)
            except (TypeError, ValueError):
                continue
            if c in valid_item_ids:
                cites.append(c)
        if not title or not cites:
            continue
        effort = str(p.get("effort", "M")).strip().upper()[:1]
        out.append({
            "title": title,
            "rationale": str(p.get("rationale", "")).strip()[:600],
            "impact": str(p.get("impact", "")).strip()[:300],
            "effort": effort if effort in ("S", "M", "L") else "M",
            "assignee": str(p.get("suggested_assignee", "")).strip(),
            "cites": cites,
        })
    return out


def run_idea_round(llm=None) -> list:
    trend_db.init_db()
    llm = llm or _default_llm
    created = []
    for beat in trend_db.beats_with_pending_items():
        items = trend_db.top_items(beat, limit=MAX_ITEMS_PER_BEAT)
        if not items:
            continue
        owner = BEAT_OWNERS.get(beat, DEFAULT_OWNER)
        system = load_persona(owner) or f"You are {owner}, an agent in Serge's business."
        try:
            raw = llm(system, build_prompt(beat, items))
        except Exception as e:
            logger.warning(f"idea LLM failed for beat {beat}: {e} — items kept for next round")
            continue  # do NOT mark items used — retried tomorrow
        proposals = parse_proposals(raw, {it["id"] for it in items})
        for p in proposals:
            assignee = p["assignee"] if p["assignee"] in VALID_ASSIGNEES else owner
            pid = trend_db.add_proposal(
                beat=beat, agent_id=owner, title=p["title"],
                rationale=p["rationale"], impact=p["impact"], effort=p["effort"],
                suggested_assignee=assignee, cited_item_ids=p["cites"])
            if pid:
                created.append(pid)
                publish_sync(owner, EV_IDEA_PROPOSED, {
                    "proposal_id": pid, "beat": beat,
                    "title": p["title"], "suggested_assignee": assignee})
        trend_db.mark_items_used([it["id"] for it in items])
    expired = trend_db.expire_stale(days=14)
    if expired:
        logger.info(f"expired {expired} stale proposals")
    logger.info(f"idea round complete: {len(created)} proposals")
    return created


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Baza idea round")
    ap.add_argument("--no-digest", action="store_true",
                    help="generate proposals but skip the Telegram digest")
    args = ap.parse_args()
    created = run_idea_round()
    print(f"created {len(created)} proposals")
    if not args.no_digest:
        from core.trend_digest import send_daily_digest
        sent = send_daily_digest()
        print(f"digest sent: {sent} proposals")


if __name__ == "__main__":
    main()
```

(Until Task 6 exists, `main()` without `--no-digest` will ImportError — that's fine; tests don't call `main`, and Task 6 creates the module. Use `--no-digest` for any manual runs before then.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_idea_engine.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add core/idea_engine.py tests/test_idea_engine.py
git commit -m "feat(trends): daily idea round — per-beat agent-persona proposals with citation validation"
```

---

### Task 6: Digest builder + sender (`core/trend_digest.py`) + idea-round systemd units

**Files:**
- Create: `core/trend_digest.py` (digest half; approval half is Task 7)
- Create: `systemd/baza-idea-round.service`
- Create: `systemd/baza-idea-round.timer`
- Test: `tests/test_trend_digest.py`

**Interfaces:**
- Consumes: `trend_db.proposals_for_digest/failing_sources/set_status` (Task 1).
- Produces:
  - `send_daily_digest(sender=None) -> int` — sends header + one message per proposal with inline keyboard; marks each sent proposal `digested`. `sender(text: str, reply_markup: dict|None) -> bool` injectable.
  - `format_proposal(p: dict) -> str`, `build_keyboard(pid: str) -> dict`, `build_header(n: int, failing: list) -> str`
  - `TREND_PROJECT_ID = "trend-ideas"` (Task 7 uses it)
  - callback_data format: `trend:approve:<pid>` / `trend:dismiss:<pid>` (Tasks 7–8 depend on this exact shape).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_trend_digest.py`:

```python
import pytest
from core import trend_db, trend_digest


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("BAZA_TREND_DB", str(tmp_path / "trend.db"))
    trend_db.init_db()


def _mk(beat, title, n):
    return trend_db.add_proposal(
        beat=beat, agent_id="a", title=title, rationale=f"reason {n}",
        impact=f"impact {n}", effort="M", suggested_assignee="claw_batto",
        cited_item_ids=[n])


def test_keyboard_callback_data_shape():
    kb = trend_digest.build_keyboard("abc123")
    row = kb["inline_keyboard"][0]
    assert row[0]["callback_data"] == "trend:approve:abc123"
    assert row[1]["callback_data"] == "trend:dismiss:abc123"


def test_header_includes_failing_sources():
    txt = trend_digest.build_header(3, [{"name": "deadfeed", "fail_count": 6}])
    assert "3 ideas" in txt and "deadfeed" in txt and "6" in txt
    assert "⚠️" in txt
    clean = trend_digest.build_header(1, [])
    assert "⚠️" not in clean and "1 idea" in clean


def test_send_daily_digest_marks_digested():
    p1 = _mk("local-ai", "Alpha unique idea one", 1)
    p2 = _mk("web-seo", "Bravo unique idea two", 2)
    sent_msgs = []
    def sender(text, reply_markup=None):
        sent_msgs.append((text, reply_markup))
        return True
    n = trend_digest.send_daily_digest(sender=sender)
    assert n == 2
    assert len(sent_msgs) == 3                      # header + 2 proposals
    assert sent_msgs[0][1] is None                  # header has no keyboard
    assert sent_msgs[1][1]["inline_keyboard"]
    assert trend_db.get_proposal(p1)["status"] == "digested"
    assert trend_db.get_proposal(p2)["status"] == "digested"


def test_send_failure_keeps_proposals_proposed():
    p1 = _mk("local-ai", "Charlie unique idea three", 3)
    n = trend_digest.send_daily_digest(sender=lambda t, r=None: False)
    assert n == 0
    assert trend_db.get_proposal(p1)["status"] == "proposed"   # retried tomorrow


def test_empty_digest_sends_nothing():
    calls = []
    n = trend_digest.send_daily_digest(sender=lambda t, r=None: calls.append(1) or True)
    assert n == 0 and calls == []


def test_format_proposal_contains_essentials():
    pid = _mk("marketing-seo", "Delta unique idea four", 4)
    txt = trend_digest.format_proposal(trend_db.get_proposal(pid))
    assert "Delta unique idea four" in txt
    assert "reason 4" in txt and "impact 4" in txt
    assert "claw_batto" in txt and "marketing-seo" in txt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_trend_digest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.trend_digest'`.

- [ ] **Step 3: Implement**

Create `core/trend_digest.py`:

```python
"""
Baza Empire — trend digest + approval handling.
Digest: header + one Telegram message per proposal with ✅/❌ inline buttons,
sent from Scout's bot (raw HTTP sendMessage — polling stays with Scout's
agent process; sending from here does not conflict).
Approval: handle_trend_callback() is registered on Scout's bot via the
BaseAgent callback hook (Task 8). callback_data: "trend:<action>:<pid>".
"""
import json
import logging
import os

import requests

from core import trend_db
from core import task_updater
from core.event_bus import publish_sync
from core.event_names import EV_IDEA_APPROVED

logger = logging.getLogger("baza.trend_digest")

DIGEST_TOKEN_ENV = "TELEGRAM_SCOUT_REEVES"
SERGE_CHAT_ID = os.getenv("SERGE_CHAT_ID", "8551331144")
TREND_PROJECT_ID = "trend-ideas"
DIGEST_LIMIT = 5
EFFORT_LABEL = {"S": "small", "M": "medium", "L": "large"}


def format_proposal(p: dict) -> str:
    effort = EFFORT_LABEL.get(p.get("effort", ""), p.get("effort", "?"))
    return (f"💡 [{p['beat']}] {p['title']}\n"
            f"{p['rationale']}\n"
            f"📈 {p['impact']}\n"
            f"🔧 effort: {effort} → {p['suggested_assignee']}")


def build_keyboard(pid: str) -> dict:
    return {"inline_keyboard": [[
        {"text": "✅ Approve", "callback_data": f"trend:approve:{pid}"},
        {"text": "❌ Dismiss", "callback_data": f"trend:dismiss:{pid}"},
    ]]}


def build_header(n: int, failing: list) -> str:
    head = f"🔭 Trend digest — {n} idea{'s' if n != 1 else ''} today. Tap to approve → task."
    if failing:
        head += "\n⚠️ failing sources: " + ", ".join(
            f"{s['name']} ({s['fail_count']}×)" for s in failing)
    return head


def _send(text: str, reply_markup: dict = None) -> bool:
    token = os.getenv(DIGEST_TOKEN_ENV)
    if not token:
        logger.warning("no TELEGRAM_SCOUT_REEVES token — digest skipped")
        return False
    payload = {"chat_id": SERGE_CHAT_ID, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json=payload, timeout=15)
        return r.ok
    except Exception as e:
        logger.error(f"digest send failed: {e}")
        return False


def send_daily_digest(sender=None) -> int:
    """Send top proposals with approve/dismiss buttons. Returns count sent.
    On send failure proposals stay 'proposed' and ride the next digest."""
    send = sender or _send
    proposals = trend_db.proposals_for_digest(limit=DIGEST_LIMIT)
    if not proposals:
        logger.info("no proposals to digest")
        return 0
    failing = trend_db.failing_sources(threshold=5)
    if not send(build_header(len(proposals), failing), None):
        return 0
    sent = 0
    for p in proposals:
        if send(format_proposal(p), build_keyboard(p["id"])):
            trend_db.set_status(p["id"], "digested")
            sent += 1
    return sent
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_trend_digest.py -v`
Expected: all 6 tests PASS. Also run `venv/bin/pytest tests/test_idea_engine.py -v` again (idea_engine `main()` imports this module now).

- [ ] **Step 5: Write the systemd units**

Create `systemd/baza-idea-round.service`:

```ini
[Unit]
Description=Baza Empire — daily idea round + trend digest
After=network.target ollama.service

[Service]
Type=oneshot
User=switchhacker
WorkingDirectory=/home/switchhacker/baza-empire/agent-framework-v3
EnvironmentFile=/home/switchhacker/baza-empire/agent-framework-v3/configs/secrets.env
ExecStart=/home/switchhacker/baza-empire/agent-framework-v3/venv/bin/python core/idea_engine.py
StandardOutput=journal
StandardError=journal
SyslogIdentifier=baza-idea-round
```

Create `systemd/baza-idea-round.timer`:

```ini
[Unit]
Description=Run the Baza idea round daily at 07:30

[Timer]
OnCalendar=*-*-* 07:30:00
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 6: Commit**

```bash
git add core/trend_digest.py tests/test_trend_digest.py systemd/baza-idea-round.service systemd/baza-idea-round.timer
git commit -m "feat(trends): daily Telegram digest with approve/dismiss inline buttons"
```

---

### Task 7: Approve → task (callback logic in `core/trend_digest.py`)

**Files:**
- Modify: `core/trend_digest.py` (append the approval half)
- Test: `tests/test_trend_callback.py`

**Interfaces:**
- Consumes: `trend_db.get_proposal/set_status/set_task_id/get_items` (Task 1), `task_updater.add_task` (existing: `add_task(project_id, title, assigned_to, description="", priority="medium", due_date="", notes="") -> str`, returns `""` on failure and already publishes `task_created`), `TREND_PROJECT_ID` (Task 6).
- Produces (used by Task 8):
  - `handle_trend_callback(data: str) -> dict` with keys `toast: str`, `text: str | None`, `ok: bool`. Pure function — no Telegram objects; safe to run in an executor thread.
  - `ensure_project() -> None` — idempotent insert of the `trend-ideas` project row.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_trend_callback.py`:

```python
import sqlite3
import pytest
from core import trend_db, trend_digest, task_updater


@pytest.fixture(autouse=True)
def tmp_dbs(tmp_path, monkeypatch):
    monkeypatch.setenv("BAZA_TREND_DB", str(tmp_path / "trend.db"))
    trend_db.init_db()
    # minimal baza_projects.db clone for tasks/projects
    tasks_db = tmp_path / "projects.db"
    con = sqlite3.connect(tasks_db)
    con.executescript("""
        CREATE TABLE projects (
            id TEXT PRIMARY KEY, name TEXT, description TEXT,
            status TEXT DEFAULT 'active', launch_date TEXT, owner TEXT,
            created_at TEXT, kind TEXT DEFAULT 'legacy-task');
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY, project_id TEXT, title TEXT, description TEXT,
            assigned_to TEXT, status TEXT DEFAULT 'pending',
            priority TEXT DEFAULT 'medium', due_date TEXT, notes TEXT,
            updated_at TEXT DEFAULT (datetime('now')));
    """)
    con.commit(); con.close()
    monkeypatch.setattr(task_updater, "DB_PATH", str(tasks_db))
    monkeypatch.setattr(trend_digest, "publish_sync", lambda *a, **k: None)


def _proposal():
    item = trend_db.add_trend_item("https://x/item", "Cited item", "src", "local-ai", 9.0)
    return trend_db.add_proposal(
        beat="local-ai", agent_id="claw_batto", title="Great unique idea",
        rationale="Because reasons.", impact="Wins", effort="S",
        suggested_assignee="claw_batto", cited_item_ids=[item])


def _task_rows():
    con = sqlite3.connect(task_updater.DB_PATH)
    rows = con.execute("SELECT id, project_id, title, assigned_to, description FROM tasks").fetchall()
    con.close()
    return rows


def test_approve_creates_exactly_one_task():
    pid = _proposal()
    res = trend_digest.handle_trend_callback(f"trend:approve:{pid}")
    assert res["ok"] is True and "✅" in (res["text"] or "")
    rows = _task_rows()
    assert len(rows) == 1
    tid, project_id, title, assignee, desc = rows[0]
    assert project_id == trend_digest.TREND_PROJECT_ID
    assert title == "Great unique idea" and assignee == "claw_batto"
    assert "https://x/item" in desc                 # cited source link included
    p = trend_db.get_proposal(pid)
    assert p["status"] == "approved" and p["task_id"] == tid


def test_double_approve_is_idempotent():
    pid = _proposal()
    trend_digest.handle_trend_callback(f"trend:approve:{pid}")
    res2 = trend_digest.handle_trend_callback(f"trend:approve:{pid}")
    assert res2["ok"] is True
    assert len(_task_rows()) == 1                   # no second task


def test_dismiss_marks_dismissed_and_creates_no_task():
    pid = _proposal()
    res = trend_digest.handle_trend_callback(f"trend:dismiss:{pid}")
    assert res["ok"] is True
    assert trend_db.get_proposal(pid)["status"] == "dismissed"
    assert _task_rows() == []


def test_unknown_or_malformed_data_is_safe():
    assert trend_digest.handle_trend_callback("trend:approve:nope")["ok"] is False
    assert trend_digest.handle_trend_callback("garbage")["ok"] is False
    assert trend_digest.handle_trend_callback("trend:frobnicate:x")["ok"] is False
    assert trend_digest.handle_trend_callback("")["ok"] is False


def test_task_failure_reverts_claim():
    pid = _proposal()
    import core.trend_digest as td
    orig = td.task_updater.add_task
    td.task_updater.add_task = lambda *a, **k: ""      # simulate insert failure
    try:
        res = td.handle_trend_callback(f"trend:approve:{pid}")
    finally:
        td.task_updater.add_task = orig
    assert res["ok"] is False
    assert trend_db.get_proposal(pid)["status"] == "digested"  # reverted, retryable
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_trend_callback.py -v`
Expected: FAIL with `AttributeError: module 'core.trend_digest' has no attribute 'handle_trend_callback'`.

- [ ] **Step 3: Implement** — append to `core/trend_digest.py`:

```python
# ── approval handling ─────────────────────────────────────────────────────────

def ensure_project() -> None:
    """Idempotent insert of the Trend Ideas project row."""
    conn = task_updater._conn()
    try:
        conn.execute("""
            INSERT OR IGNORE INTO projects (id, name, description, status, owner, kind)
            VALUES (?, 'Trend Ideas', 'Ideas approved from the trend digest',
                    'active', 'scout_reeves', 'legacy-task')
        """, (TREND_PROJECT_ID,))
        conn.commit()
    finally:
        conn.close()


def _cited_links(p: dict) -> list:
    try:
        ids = json.loads(p.get("cited_item_ids") or "[]")
    except Exception:
        ids = []
    return [it["url"] for it in trend_db.get_items(ids)]


def handle_trend_callback(data: str) -> dict:
    """Handle 'trend:<action>:<pid>' button presses. Idempotent.
    Returns {'toast': str, 'text': str|None, 'ok': bool} — toast is the
    short popup, text (when set) replaces the proposal message."""
    parts = (data or "").split(":", 2)
    if len(parts) != 3 or parts[0] != "trend" or parts[1] not in ("approve", "dismiss"):
        return {"toast": "unrecognized action", "text": None, "ok": False}
    _, action, pid = parts
    p = trend_db.get_proposal(pid)
    if p is None:
        return {"toast": "proposal not found", "text": None, "ok": False}

    if action == "dismiss":
        if trend_db.set_status(pid, "dismissed"):
            return {"toast": "dismissed", "text": f"❌ Dismissed: {p['title']}", "ok": True}
        return {"toast": f"cannot dismiss ({p['status']})", "text": None,
                "ok": p["status"] == "dismissed"}

    # approve — claim status FIRST (atomic), then create the task
    if p["status"] == "approved":
        return {"toast": "already approved", "text": None, "ok": True}
    if not trend_db.set_status(pid, "approved"):
        return {"toast": f"cannot approve ({p['status']})", "text": None, "ok": False}
    ensure_project()
    links = _cited_links(p)
    desc = (f"{p['rationale']}\n\nExpected impact: {p['impact']}\n"
            f"Effort: {p['effort']}\nProposed by: {p['agent_id']} (trend digest)\n"
            "Sources:\n" + "\n".join(f"- {u}" for u in links))
    task_id = task_updater.add_task(
        TREND_PROJECT_ID, p["title"], p["suggested_assignee"],
        description=desc, priority="medium")
    if not task_id:
        # revert the claim so the button can be retried
        trend_db.set_task_id(pid, None)
        with trend_db._conn() as c:
            c.execute("UPDATE idea_proposals SET status='digested' WHERE id=?", (pid,))
        return {"toast": "task creation failed — try again", "text": None, "ok": False}
    trend_db.set_task_id(pid, task_id)
    publish_sync("scout_reeves", EV_IDEA_APPROVED, {
        "proposal_id": pid, "task_id": task_id,
        "title": p["title"], "assignee": p["suggested_assignee"]})
    return {"toast": "task created",
            "text": (f"✅ Task created for {p['suggested_assignee']}: {p['title']}\n"
                     f"(task {task_id[:8]}, project Trend Ideas)"),
            "ok": True}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_trend_callback.py tests/test_trend_digest.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add core/trend_digest.py tests/test_trend_callback.py
git commit -m "feat(trends): approve→task callback logic (idempotent, claim-first, revert on failure)"
```

---

### Task 8: BaseAgent callback-query hook + Scout registration

**Files:**
- Modify: `core/base_agent.py:35` (imports), `core/base_agent.py:~1971` (Bot Runner section: new methods + handler registration in `run()`)
- Modify: `agents/scout_reeves/agent.py:440-458` (`run()`: add CallbackQueryHandler + register trend handler)
- Test: `tests/test_callback_hook.py`

**Interfaces:**
- Consumes: `handle_trend_callback` (Task 7).
- Produces: generic platform hook any BaseAgent bot can use:
  - `BaseAgent.register_callback_handler(prefix: str, fn)` — `fn(data: str) -> dict` (sync; run in executor) returning `{'toast', 'text', 'ok'}`.
  - `BaseAgent._handle_callback_query(update, context)` — PTB handler; dispatches by longest matching prefix; answers the query; edits the message when `text` is set.

- [ ] **Step 1: Write the failing test**

Create `tests/test_callback_hook.py`:

```python
import asyncio
import pytest
from core.base_agent import BaseAgent


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.answers = []
        self.edits = []
    async def answer(self, text="", show_alert=False):
        self.answers.append(text)
    async def edit_message_text(self, text):
        self.edits.append(text)


class FakeUpdate:
    def __init__(self, data):
        self.callback_query = FakeQuery(data)


def _bare_agent():
    # bypass __init__ (it wires DBs/Telegram); the hook must not depend on it
    return BaseAgent.__new__(BaseAgent)


def test_register_and_dispatch():
    agent = _bare_agent()
    agent.register_callback_handler("trend:", lambda data: {
        "toast": "done", "text": f"handled {data}", "ok": True})
    upd = FakeUpdate("trend:approve:abc")
    asyncio.run(agent._handle_callback_query(upd, None))
    assert upd.callback_query.answers == ["done"]
    assert upd.callback_query.edits == ["handled trend:approve:abc"]


def test_unmatched_prefix_just_answers():
    agent = _bare_agent()
    agent.register_callback_handler("trend:", lambda data: {"toast": "x", "text": "y", "ok": True})
    upd = FakeUpdate("other:thing")
    asyncio.run(agent._handle_callback_query(upd, None))
    assert upd.callback_query.answers == [""]      # acked, nothing else
    assert upd.callback_query.edits == []


def test_handler_exception_is_contained():
    agent = _bare_agent()
    def boom(data): raise RuntimeError("nope")
    agent.register_callback_handler("trend:", boom)
    upd = FakeUpdate("trend:approve:abc")
    asyncio.run(agent._handle_callback_query(upd, None))   # must not raise
    assert any("rror" in a for a in upd.callback_query.answers)


def test_no_text_means_no_edit():
    agent = _bare_agent()
    agent.register_callback_handler("t:", lambda d: {"toast": "ok", "text": None, "ok": True})
    upd = FakeUpdate("t:x")
    asyncio.run(agent._handle_callback_query(upd, None))
    assert upd.callback_query.edits == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/test_callback_hook.py -v`
Expected: FAIL with `AttributeError: ... has no attribute 'register_callback_handler'`.

- [ ] **Step 3: Implement the hook in `core/base_agent.py`**

Change line 35 from:

```python
from telegram.ext import Application, MessageHandler, filters, ContextTypes
```

to:

```python
from telegram.ext import Application, CallbackQueryHandler, MessageHandler, filters, ContextTypes
```

Insert immediately before the `# ── Bot Runner ────` comment (line ~1971):

```python
    # ── Inline-button callbacks ───────────────────────────────────────────────

    def register_callback_handler(self, prefix: str, fn):
        """Register fn(data:str)->{'toast','text','ok'} for callback_data
        starting with `prefix`. fn runs in an executor thread (may block)."""
        if not hasattr(self, "_callback_handlers"):
            self._callback_handlers = {}
        self._callback_handlers[prefix] = fn

    async def _handle_callback_query(self, update, context):
        query = update.callback_query
        data = query.data or ""
        handler = None
        # longest prefix wins so "trend:x:" can coexist with "trend:"
        for prefix in sorted(getattr(self, "_callback_handlers", {}), key=len, reverse=True):
            if data.startswith(prefix):
                handler = self._callback_handlers[prefix]
                break
        if handler is None:
            await query.answer("")
            return
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, handler, data)
            await query.answer((result.get("toast") or "")[:190])
            if result.get("text"):
                await query.edit_message_text(result["text"][:4000])
        except Exception as e:
            logger.exception(f"callback handler failed for {data[:60]}: {e}")
            try:
                await query.answer("Error — see logs", show_alert=True)
            except Exception:
                pass
```

In `BaseAgent.run()` (line ~1979), after the attachment MessageHandler add:

```python
        app.add_handler(CallbackQueryHandler(self._handle_callback_query))
```

- [ ] **Step 4: Wire Scout** — in `agents/scout_reeves/agent.py` `run()` (line ~440), change:

```python
        from telegram.ext import Application, MessageHandler, filters
        app = Application.builder().token(token).build()
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
```

to:

```python
        from telegram.ext import Application, CallbackQueryHandler, MessageHandler, filters
        from core.trend_digest import handle_trend_callback
        self.register_callback_handler("trend:", handle_trend_callback)
        app = Application.builder().token(token).build()
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        app.add_handler(CallbackQueryHandler(self._handle_callback_query))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_callback_hook.py -v`
Expected: 4 PASS.
Also: `venv/bin/python -c "from agents.scout_reeves.agent import ScoutReeves; print('import ok')"`
Expected: `import ok` (catches wiring typos without starting the bot).

- [ ] **Step 6: Commit**

```bash
git add core/base_agent.py agents/scout_reeves/agent.py tests/test_callback_hook.py
git commit -m "feat(agents): generic BaseAgent inline-button hook; Scout handles trend approve/dismiss"
```

---

### Task 9: Task leases (no double-runs)

**Files:**
- Modify: `core/task_updater.py` (add `ensure_lease_columns`, `acquire_lease`, `release_lease`)
- Modify: `core/task_runner.py` (acquire/release around per-task execution; `ensure_lease_columns()` in `main()`)
- Test: `tests/test_task_lease.py`

**Interfaces:**
- Produces:
  - `task_updater.ensure_lease_columns() -> None` — idempotent `ALTER TABLE tasks ADD COLUMN lease_owner TEXT / lease_until TEXT`.
  - `task_updater.acquire_lease(task_id: str, owner: str, ttl_minutes: int = 45) -> bool` — atomic UPDATE; True iff this caller now holds the lease. Expired leases are reclaimable.
  - `task_updater.release_lease(task_id: str, owner: str) -> None` — only the owner's lease is cleared.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_task_lease.py`:

```python
import sqlite3
import pytest
from core import task_updater


@pytest.fixture(autouse=True)
def tmp_tasks_db(tmp_path, monkeypatch):
    db = tmp_path / "projects.db"
    con = sqlite3.connect(db)
    con.execute("""
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY, project_id TEXT, title TEXT, description TEXT,
            assigned_to TEXT, status TEXT DEFAULT 'pending',
            priority TEXT DEFAULT 'medium', due_date TEXT, notes TEXT,
            updated_at TEXT DEFAULT (datetime('now')))""")
    con.execute("INSERT INTO tasks (id, title, assigned_to) VALUES ('t1', 'Test', 'claw_batto')")
    con.commit(); con.close()
    monkeypatch.setattr(task_updater, "DB_PATH", str(db))
    task_updater.ensure_lease_columns()


def test_ensure_lease_columns_idempotent():
    task_updater.ensure_lease_columns()   # second call must not raise
    con = sqlite3.connect(task_updater.DB_PATH)
    cols = {r[1] for r in con.execute("PRAGMA table_info(tasks)")}
    con.close()
    assert {"lease_owner", "lease_until"} <= cols


def test_second_acquire_fails_while_leased():
    assert task_updater.acquire_lease("t1", "runner-A") is True
    assert task_updater.acquire_lease("t1", "runner-B") is False


def test_release_frees_lease_only_for_owner():
    task_updater.acquire_lease("t1", "runner-A")
    task_updater.release_lease("t1", "runner-B")   # not the owner — no-op
    assert task_updater.acquire_lease("t1", "runner-B") is False
    task_updater.release_lease("t1", "runner-A")
    assert task_updater.acquire_lease("t1", "runner-B") is True


def test_expired_lease_is_reclaimable():
    task_updater.acquire_lease("t1", "runner-A", ttl_minutes=45)
    con = sqlite3.connect(task_updater.DB_PATH)
    con.execute("UPDATE tasks SET lease_until=datetime('now','-1 minute') WHERE id='t1'")
    con.commit(); con.close()
    assert task_updater.acquire_lease("t1", "runner-B") is True


def test_acquire_same_owner_renews():
    assert task_updater.acquire_lease("t1", "runner-A") is True
    assert task_updater.acquire_lease("t1", "runner-A") is True   # renewal allowed
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_task_lease.py -v`
Expected: FAIL with `AttributeError: module 'core.task_updater' has no attribute 'ensure_lease_columns'`.

- [ ] **Step 3: Implement in `core/task_updater.py`** — append after `add_task` (before the `AgentTaskManager` section):

```python
# ── Task leases (double-run guard) ─────────────────────────────────────────────

def ensure_lease_columns() -> None:
    """Idempotently add lease columns to tasks (older DBs lack them)."""
    conn = _conn()
    try:
        for col in ("lease_owner TEXT", "lease_until TEXT"):
            try:
                conn.execute(f"ALTER TABLE tasks ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass  # already exists
        conn.commit()
    finally:
        conn.close()


def acquire_lease(task_id: str, owner: str, ttl_minutes: int = 45) -> bool:
    """Atomically claim a task for `owner`. True iff the lease is now held by
    this owner. Free, expired, or own leases are (re)claimable; a live lease
    held by someone else is not."""
    conn = _conn()
    try:
        cur = conn.execute("""
            UPDATE tasks SET lease_owner=?, lease_until=datetime('now', ?)
            WHERE id=? AND (lease_owner IS NULL OR lease_owner=?
                            OR lease_until IS NULL OR lease_until < datetime('now'))
        """, (owner, f"+{int(ttl_minutes)} minutes", task_id, owner))
        conn.commit()
        return cur.rowcount == 1
    except Exception as e:
        logger.warning(f"[task_updater] acquire_lease error: {e}")
        return False
    finally:
        conn.close()


def release_lease(task_id: str, owner: str) -> None:
    conn = _conn()
    try:
        conn.execute(
            "UPDATE tasks SET lease_owner=NULL, lease_until=NULL"
            " WHERE id=? AND lease_owner=?", (task_id, owner))
        conn.commit()
    except Exception as e:
        logger.warning(f"[task_updater] release_lease error: {e}")
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_task_lease.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Wire into `core/task_runner.py`**

1. Extend the existing import at line ~36 to include the new functions:
   `from core.task_updater import (get_my_tasks, update_task, complete_task, ..., acquire_lease, release_lease, ensure_lease_columns)` (keep the existing names, add the three new ones).
2. In `run_agent_tasks` (`core/task_runner.py:639`) the function builds `tasks` (pending) + `in_prog` lists (lines 647-649) and then iterates them in a per-task loop. Find that loop (`for task in ...:`) and wrap its body:

```python
        owner = f"task_runner:{os.getpid()}"
        if not acquire_lease(task["id"], owner):
            logger.info(f"  lease held elsewhere — skipping {task['id'][:8]}")
            continue
        try:
            # ── existing per-task body, indented one level ──
            ...
        finally:
            release_lease(task["id"], owner)
```

(Keep every existing statement of the body intact — only indent it under the `try:` and add the lease guard around it. If the function has more than one loop that *executes* tasks, guard each; do not guard loops that only summarize/notify.)

3. In `main()` (line ~925), add `ensure_lease_columns()` as the first statement after arg parsing.

- [ ] **Step 6: Verify nothing broke**

Run: `venv/bin/pytest tests/test_task_lease.py -v && venv/bin/python core/task_runner.py --dry-run`
Expected: tests PASS; dry-run exits cleanly (it lists what it would do without LLM calls).

- [ ] **Step 7: Commit**

```bash
git add core/task_updater.py core/task_runner.py tests/test_task_lease.py
git commit -m "feat(tasks): lease/lock so concurrent runners can't double-run a task"
```

---

### Task 10: Anchored completion signals

**Files:**
- Modify: `core/task_runner.py:410-429` (extract + anchor the signal parsing)
- Test: `tests/test_task_signals.py`

**Interfaces:**
- Produces: `task_runner.parse_completion_signals(output: str) -> dict` with keys `completed, in_progress, blocked, block_reason, clean_output`. Signals match **only at line start** (leading whitespace allowed).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_task_signals.py`:

```python
from core.task_runner import parse_completion_signals


def test_anchored_complete_detected():
    sig = parse_completion_signals("Deliverable text here.\nTASK_COMPLETE\n")
    assert sig["completed"] is True
    assert "TASK_COMPLETE" not in sig["clean_output"]
    assert "Deliverable text" in sig["clean_output"]


def test_mid_prose_mention_is_not_a_signal():
    out = ("I will write TASK_COMPLETE when done. The plan mentions "
           "TASK_BLOCKED: handling too.\nStill working on it.")
    sig = parse_completion_signals(out)
    assert sig["completed"] is False
    assert sig["blocked"] is False
    assert sig["in_progress"] is False
    assert "Still working" in sig["clean_output"]


def test_blocked_reason_extracted():
    sig = parse_completion_signals("Tried X.\nTASK_BLOCKED: missing API key\n")
    assert sig["blocked"] is True
    assert sig["block_reason"] == "missing API key"


def test_in_progress_with_leading_whitespace():
    sig = parse_completion_signals("did stuff\n   TASK_IN_PROGRESS\n")
    assert sig["in_progress"] is True


def test_signal_lines_stripped_from_clean_output():
    out = "line one\nTASK_IN_PROGRESS\nline two\nTASK_COMPLETE"
    sig = parse_completion_signals(out)
    assert sig["clean_output"] == "line one\nline two"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_task_signals.py -v`
Expected: FAIL with `ImportError: cannot import name 'parse_completion_signals'`.

- [ ] **Step 3: Implement**

In `core/task_runner.py`: ensure `import re` is at the top of the file (add it to the import block at lines 19-28 if not present). Then add near the other module-level helpers (e.g. right above `notify_serge`, line ~447):

```python
# Completion signals must be ANCHORED at line start — a prose mention like
# "I will write TASK_COMPLETE when done" must not complete the task.
_SIG_COMPLETE = re.compile(r"^[ \t]*TASK_COMPLETE\b")
_SIG_PROGRESS = re.compile(r"^[ \t]*TASK_IN_PROGRESS\b")
_SIG_BLOCKED = re.compile(r"^[ \t]*TASK_BLOCKED:\s*(.*)$")
_SIG_ANY = re.compile(r"^[ \t]*(TASK_COMPLETE\b|TASK_IN_PROGRESS\b|TASK_BLOCKED:)")


def parse_completion_signals(output: str) -> dict:
    completed = in_progress = blocked = False
    block_reason = ""
    clean_lines = []
    for line in (output or "").split("\n"):
        m = _SIG_BLOCKED.match(line)
        if m:
            blocked = True
            block_reason = block_reason or m.group(1).strip()
            continue
        if _SIG_COMPLETE.match(line):
            completed = True
            continue
        if _SIG_PROGRESS.match(line):
            in_progress = True
            continue
        if _SIG_ANY.match(line):
            continue
        clean_lines.append(line)
    return {"completed": completed, "in_progress": in_progress,
            "blocked": blocked, "block_reason": block_reason,
            "clean_output": "\n".join(clean_lines).strip()}
```

Then replace the parse block at lines 410-429 (from `# Parse completion signal` through the `clean_output = ...` statement) with:

```python
        sig = parse_completion_signals(output)
        completed = sig["completed"]
        blocked = sig["blocked"]
        in_progress = sig["in_progress"]
        block_reason = sig["block_reason"]
        clean_output = sig["clean_output"]
```

Also grep for other substring checks in the same file — `grep -n '"TASK_COMPLETE" in\|"TASK_BLOCKED" in\|TASK_COMPLETE.*in output' core/task_runner.py` — and convert any *other execution-path* check to `parse_completion_signals` too (prompt-text lines 237/311-328 are instructions to the LLM — leave those).

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_task_signals.py -v && venv/bin/python -c "import core.task_runner; print('import ok')"`
Expected: 5 PASS + `import ok`.

- [ ] **Step 5: Commit**

```bash
git add core/task_runner.py tests/test_task_signals.py
git commit -m "fix(tasks): anchor TASK_COMPLETE/BLOCKED/IN_PROGRESS parsing to line start (kills false positives)"
```

---

### Task 11: Skills-engine guards (arg validation, output cap, error kinds)

**Files:**
- Modify: `core/skills_engine.py` (`run()` at line 69, `parse_and_run()` at line 144)
- Test: `tests/test_skills_guards.py`

**Interfaces:**
- Produces (backward-compatible — existing keys `success/output/error` unchanged):
  - Every failure result gains `"kind"` ∈ `{"not_found", "bad_args", "timeout", "nonzero_exit", "exception"}`.
  - `run(..., timeout: int = None)` — optional per-call timeout override (tests use it; default behavior unchanged).
  - stdout capped at `SKILL_OUTPUT_CAP` bytes (env, default 32768) with a `[truncated N bytes]` suffix.
  - Non-dict JSON args (e.g. `[1,2]`) rejected before spawn with `kind="bad_args"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_skills_guards.py`:

```python
import os
import stat
import pytest
from core import skills_engine
from core.skills_engine import SkillsEngine


@pytest.fixture
def engine(tmp_path, monkeypatch):
    # isolate skill dirs + neutralize DB/journal side effects
    shared = tmp_path / "shared"; shared.mkdir()
    agent_dir = tmp_path / "agent_skills"; agent_dir.mkdir()
    monkeypatch.setattr(skills_engine, "SKILLS_SHARED_DIR", str(shared))
    monkeypatch.setattr(skills_engine, "skill_ran", lambda *a, **k: None)
    monkeypatch.setattr(skills_engine, "journal_log", lambda *a, **k: None)
    monkeypatch.setattr(skills_engine, "_task_events", None)
    eng = SkillsEngine("test_agent")
    eng.agent_skills_dir = str(agent_dir)
    return eng, shared


def _write_skill(shared, name, body):
    p = shared / f"{name}.py"
    p.write_text(body)
    return p


def test_not_found_kind(engine):
    eng, _ = engine
    r = eng.run("no_such_skill", {})
    assert r["success"] is False and r["kind"] == "not_found"


def test_bad_args_rejected_before_spawn(engine):
    eng, shared = engine
    _write_skill(shared, "echoer", "print('ran anyway')")
    r = eng.run("echoer", [1, 2, 3])          # list, not dict
    assert r["success"] is False and r["kind"] == "bad_args"
    assert "ran anyway" not in (r.get("output") or "")


def test_output_capped(engine, monkeypatch):
    eng, shared = engine
    monkeypatch.setenv("SKILL_OUTPUT_CAP", "1000")
    _write_skill(shared, "bigmouth", "print('x' * 50000)")
    r = eng.run("bigmouth", {})
    assert r["success"] is True
    assert len(r["output"]) < 1200
    assert "[truncated" in r["output"]


def test_nonzero_exit_kind(engine):
    eng, shared = engine
    _write_skill(shared, "failer", "import sys; sys.stderr.write('boom'); sys.exit(2)")
    r = eng.run("failer", {})
    assert r["success"] is False and r["kind"] == "nonzero_exit"
    assert "boom" in r["error"]


def test_timeout_kind(engine):
    eng, shared = engine
    _write_skill(shared, "sleeper", "import time; time.sleep(5)")
    r = eng.run("sleeper", {}, timeout=1)
    assert r["success"] is False and r["kind"] == "timeout"


def test_parse_and_run_rejects_non_dict_json(engine):
    eng, shared = engine
    _write_skill(shared, "echoer2", "print('should not run')")
    text, results = eng.parse_and_run('##SKILL:echoer2[1,2]##')
    # [1,2] is not a JSON object → no args extracted → runs with {} (existing
    # brace-scanner only extracts {...}); the guard applies to run() input.
    r = eng.run("echoer2", "not-a-dict")
    assert r["kind"] == "bad_args"


def test_good_path_unchanged(engine):
    eng, shared = engine
    _write_skill(shared, "greeter",
                 "import os, json\n"
                 "args = json.loads(os.environ.get('SKILL_ARGS', '{}'))\n"
                 "print('hello ' + args.get('who', '?'))")
    r = eng.run("greeter", {"who": "serge"})
    assert r["success"] is True and r["output"] == "hello serge"
    assert "kind" not in r
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_skills_guards.py -v`
Expected: FAILs — `KeyError: 'kind'` / missing timeout param / no truncation.

- [ ] **Step 3: Implement in `core/skills_engine.py`**

In `run()` (line 69) apply these changes:

1. Signature → `def run(self, skill_name, args={}, chat_id=None, task_id=None, project_id=None, timeout=None):`
2. The not-found return (lines 71-82): add `"kind": "not_found"` to the returned dict.
3. Immediately after the not-found check, before `start = time.time()`, insert:

```python
        if not isinstance(args, dict):
            return {"success": False, "kind": "bad_args", "output": "",
                    "error": f"skill args must be a JSON object, got {type(args).__name__}"}
```

4. Timeout line (97): `skill_timeout = timeout or (600 if any(kw in skill_name for kw in ("image", "generate", "render", "enhance")) else 90)`
5. After `stdout_clean = (proc.stdout or "").strip()` (line 101), insert the cap:

```python
            cap = int(os.environ.get("SKILL_OUTPUT_CAP", "32768"))
            if len(stdout_clean) > cap:
                dropped = len(stdout_clean) - cap
                stdout_clean = stdout_clean[:cap] + f"\n[truncated {dropped} bytes]"
```

6. In the failure branch (line 110 `if not success:`), add `result["kind"] = "nonzero_exit"`.
7. In the `TimeoutExpired` handler (line 125), add `"kind": "timeout"` to the returned dict.
8. In the generic `except Exception` handler (line 134), add `"kind": "exception"` to the returned dict.

In `parse_and_run()` (line 171-177), after `args = json.loads(json_str)` succeeds, add a dict check:

```python
                try:
                    args = json.loads(json_str)
                    parse_error = None
                    if not isinstance(args, dict):
                        args, parse_error = {}, (
                            f"skill args must be a JSON object, got {type(args).__name__}")
                except json.JSONDecodeError as e:
                    ...  # existing branch unchanged
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_skills_guards.py -v`
Expected: 7 PASS.

- [ ] **Step 5: Regression-check existing consumers**

Run: `venv/bin/pytest tests/ -k "skill" -v`
Expected: all existing skill-related tests still PASS (the new `kind` key is additive; `success/output/error` semantics unchanged).

- [ ] **Step 6: Commit**

```bash
git add core/skills_engine.py tests/test_skills_guards.py
git commit -m "feat(skills): arg validation, 32KB output cap, structured error kinds"
```

---

### Task 12: Deploy, live verification, session log

**Files:**
- No new code. Installs units, runs the pipeline live, restarts Scout.

- [ ] **Step 1: Full test suite**

Run: `venv/bin/pytest tests/ -q`
Expected: everything passes (pre-existing failures, if any, must match a `git stash`-baseline run — new failures are yours to fix before proceeding).

- [ ] **Step 2: Install + start the timers**

```bash
sudo cp systemd/baza-trend-scout.service systemd/baza-trend-scout.timer \
        systemd/baza-idea-round.service systemd/baza-idea-round.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now baza-trend-scout.timer baza-idea-round.timer
systemctl list-timers 'baza-trend*' 'baza-idea*'
```

Expected: both timers listed with next-run times.

- [ ] **Step 3: First live scan**

```bash
sudo systemctl start baza-trend-scout.service
journalctl -u baza-trend-scout -n 30 --no-pager
venv/bin/python -c "
from core import trend_db
print('beats pending:', trend_db.beats_with_pending_items())
for b in trend_db.beats_with_pending_items():
    for it in trend_db.top_items(b, 3):
        print(f'  [{b}] {it[\"score\"]:.0f} {it[\"title\"][:70]}')"
```

Expected: `scan complete: {...}` in journal with `stored > 0`; items print. If `stored == 0` on a healthy scan, check the journal for `score_batch` warnings (model JSON compliance) before touching thresholds.

- [ ] **Step 4: Restart Scout (picks up the callback handler)**

```bash
sudo systemctl restart baza-agent-scout-reeves.service
journalctl -u baza-agent-scout-reeves -n 20 --no-pager
```

Expected: normal startup, no tracebacks. (Check the exact unit name first with `systemctl list-units 'baza-agent-*'` — use the Scout unit as listed.)

- [ ] **Step 5: First live idea round + digest**

```bash
sudo systemctl start baza-idea-round.service
journalctl -u baza-idea-round -n 30 --no-pager
```

Expected: `created N proposals` and `digest sent: N proposals`; Serge receives the digest on Telegram from Scout's bot with working ✅/❌ buttons. Ask Serge to tap ✅ on one idea; verify a task appears:

```bash
sqlite3 dashboard/baza_projects.db "SELECT id, title, assigned_to, status FROM tasks WHERE project_id='trend-ideas'"
```

- [ ] **Step 6: Session log + commit any deploy-time fixes**

Append to `~/Desktop/baza-session-log.md` (timestamp from `date '+%Y-%m-%d %H:%M'`): units installed, first-scan counts, digest result, any feed replacements made.

```bash
git status --short   # commit stragglers with targeted adds if any
```

---

## Plan Self-Review Notes

- **Spec coverage:** storage (T1), registry+fetch (T2), event constants + BaseAgent subscription + publish-failure logging (T3 — async `EventBus.publish` already logs failures at `event_bus.py:115`, so only constants/subscription were genuinely missing), scanner+timer (T4), idea round (T5), digest (T6), approve→task (T7), callback hook (T8), leases (T9), anchored signals (T10), skills guards (T11), deploy+e2e (T12). Spec's "http_json" source type dropped (YAGNI — no seed source needed it; rss/hn/reddit_json cover the registry).
- **Known deferred detail (by design):** final feed list is verified live in Task 2 Step 7 — dead candidates get replaced per-beat.
- **Type consistency check done:** `handle_trend_callback` returns `{'toast','text','ok'}` and Task 8's hook consumes exactly those keys; `score_batch` returns `None|list` and `run_scan` branches on `None`; `add_task` returns `""` on failure and Task 7 treats falsy as failure.
