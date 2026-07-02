# Telegram Rich Text Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** All outbound Telegram messages from the Baza agents render rich text (bold, • lists, ✅ checks, code) via one shared markdown→HTML formatter with tag-safe chunking and a plain-text fallback.

**Architecture:** New `core/telegram_fmt.py` converts LLM-flavored markdown to Telegram HTML and owns chunking + send (async `send_html` for PTB bots, sync `post_html` for cron scripts). All ten existing send paths route through it. A house-style block appended in `context_mixin.get_system_prompt()` makes agents produce organized output.

**Tech Stack:** Python 3, stdlib `re`/`html`, `requests` (already in venv), python-telegram-bot v20 (already in use), pytest.

**Spec:** `docs/superpowers/specs/2026-07-02-telegram-rich-text-design.md`

## Global Constraints

- Repo root: `/home/switchhacker/baza-empire/agent-framework-v3` — all paths below relative to it. Run everything from this directory.
- Test command: `venv/bin/pytest <file> -v`
- **DO NOT `git commit` or `git push`** — the `claw-auto-git` user timer commits this repo hourly. Where a normal plan says "Commit", just verify tests pass and move on.
- No new pip dependencies. Local-first.
- Telegram hard message limit 4096; we chunk at 4000.
- Allowed Telegram HTML tags: `<b> <i> <u> <s> <code> <pre> <a> <blockquote> <tg-spoiler>`. Nothing else (no `<br>`, no `<ul>`) — newlines are literal.
- Agent service unit names use **hyphens** (per 2026-07-01 handoff). Do not guess: get exact names with `systemctl list-units 'baza-agent-*' --no-legend` and restart exactly those.
- Never break a send: every new path must fall back to plain text on Telegram parse errors, and top-level exceptions must be caught/logged like the code they replace.

---

### Task 1: `core/telegram_fmt.py` — markdown→HTML converter

**Files:**
- Create: `core/telegram_fmt.py`
- Test: `tests/test_telegram_fmt.py`

**Interfaces:**
- Produces: `md_to_html(text: str) -> str` — Telegram-safe HTML string.
- Produces: `strip_markdown(text: str) -> str` — plain text (relocated from base_agent, same behavior).
- Produces: `html_to_plain(html_text: str) -> str` — strips tags + unescapes entities (fallback path).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_telegram_fmt.py`:

```python
"""Tests for core/telegram_fmt.py — markdown → Telegram HTML."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.telegram_fmt import md_to_html, strip_markdown, html_to_plain


# ── escaping ────────────────────────────────────────────────────────────
def test_escapes_html_specials():
    assert md_to_html("a < b & c > d") == "a &lt; b &amp; c &gt; d"

def test_escapes_inside_inline_code():
    assert md_to_html("run `a && b < c`") == "run <code>a &amp;&amp; b &lt; c</code>"

# ── inline styles ───────────────────────────────────────────────────────
def test_bold():
    assert md_to_html("**hello** and __world__") == "<b>hello</b> and <b>world</b>"

def test_italic():
    assert md_to_html("*hi* and _there_") == "<i>hi</i> and <i>there</i>"

def test_underscores_inside_words_untouched():
    assert md_to_html("baza_projects.db and claw_batto") == "baza_projects.db and claw_batto"

def test_bold_italic_not_confused():
    assert md_to_html("**bold** then *ital*") == "<b>bold</b> then <i>ital</i>"

# ── code blocks ─────────────────────────────────────────────────────────
def test_fenced_block():
    out = md_to_html("```\nx = 1 < 2\n```")
    assert out == "<pre>x = 1 &lt; 2\n</pre>"

def test_fenced_block_with_language():
    out = md_to_html("```python\nprint(1)\n```")
    assert out == '<pre><code class="language-python">print(1)\n</code></pre>'

def test_no_markdown_conversion_inside_fence():
    out = md_to_html("```\n**not bold**\n```")
    assert "<b>" not in out and "**not bold**" in out

# ── headers / bullets / checklists / hr ─────────────────────────────────
def test_header_becomes_bold_line():
    assert md_to_html("### Status Report") == "<b>Status Report</b>"

def test_dash_bullet_becomes_dot():
    assert md_to_html("- one\n- two") == "• one\n• two"

def test_star_bullet_becomes_dot():
    assert md_to_html("* one") == "• one"

def test_nested_bullet_keeps_indent():
    assert md_to_html("- a\n  - b") == "• a\n  • b"

def test_checklist():
    assert md_to_html("- [x] done\n- [ ] todo") == "✅ done\n☐ todo"

def test_horizontal_rule():
    assert md_to_html("a\n---\nb") == "a\n───────\nb"

def test_numbered_list_untouched():
    assert md_to_html("1. one\n2. two") == "1. one\n2. two"

# ── links ───────────────────────────────────────────────────────────────
def test_http_link():
    assert md_to_html("[site](https://ahb123.com)") == '<a href="https://ahb123.com">site</a>'

def test_javascript_link_rejected():
    out = md_to_html("[x](javascript:alert(1))")
    assert "<a" not in out and "x" in out

# ── tables → pre ────────────────────────────────────────────────────────
def test_table_becomes_pre():
    md = "| a | b |\n|---|---|\n| 1 | 2 |"
    out = md_to_html(md)
    assert out.startswith("<pre>") and out.endswith("</pre>") and "| 1 | 2 |" in out

# ── idempotence guard ───────────────────────────────────────────────────
def test_existing_html_passes_through():
    src = "<b>Done:</b> restarted <code>baza-dashboard</code>"
    assert md_to_html(src) == src

# ── helpers ─────────────────────────────────────────────────────────────
def test_strip_markdown():
    assert strip_markdown("### H\n**b** `c` [t](http://x)\n- item") == "H\nb c t\nitem"

def test_html_to_plain():
    assert html_to_plain("<b>a &amp; b</b>\n<pre>c</pre>") == "a & b\nc"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_telegram_fmt.py -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'core.telegram_fmt'`

- [ ] **Step 3: Write the implementation**

Create `core/telegram_fmt.py`:

```python
"""Markdown → Telegram HTML formatting + safe senders for all outbound paths.

LLM agents write markdown; Telegram renders HTML. This module converts
between them, chunks at the 4096 limit without splitting tags, and falls
back to plain text on Telegram parse errors so a message is never lost.
"""
from __future__ import annotations

import asyncio
import html as _html
import logging
import re
import time

logger = logging.getLogger(__name__)

MAX_LEN = 4000

_HTML_TAG_RE = re.compile(r"</?(b|i|u|s|code|pre|a|blockquote|tg-spoiler)\b", re.I)
_MD_MARK_RE = re.compile(
    r"(\*\*.+?\*\*|__.+?__|^#{1,6}\s|^\s*[-*]\s|```|\[[^\]]+\]\([^)]+\))", re.M
)


def md_to_html(text: str) -> str:
    """Convert LLM-flavored markdown to Telegram-safe HTML."""
    if not isinstance(text, str):
        text = str(text)
    # Pass-through: already Telegram HTML with no markdown markers
    if _HTML_TAG_RE.search(text) and not _MD_MARK_RE.search(text):
        return text

    stash: list[str] = []

    def _hold(fragment: str) -> str:
        stash.append(fragment)
        return f"\x00{len(stash) - 1}\x00"

    # 1. Fenced code blocks first (no inner conversion)
    def _fence(m):
        lang = (m.group(1) or "").strip()
        code = _html.escape(m.group(2))
        if lang:
            return _hold(f'<pre><code class="language-{lang}">{code}</code></pre>')
        return _hold(f"<pre>{code}</pre>")

    text = re.sub(r"```([^\n`]*)\n(.*?)```", _fence, text, flags=re.DOTALL)

    # 2. Inline code (no inner conversion)
    text = re.sub(
        r"`([^`\n]+)`", lambda m: _hold(f"<code>{_html.escape(m.group(1))}</code>"), text
    )

    # 3. Tables → <pre> (contiguous lines that start and end with |)
    def _table(m):
        return _hold(f"<pre>{_html.escape(m.group(0).rstrip())}</pre>")

    text = re.sub(r"(?:^\|.*\|[ \t]*$\n?){2,}", _table, text, flags=re.M)

    # 4. Escape everything else
    text = _html.escape(text, quote=False)

    # 5. Links (http/https only; other schemes degrade to plain text)
    def _link(m):
        label, url = m.group(1), m.group(2)
        if re.match(r"https?://", url, re.I):
            return _hold(f'<a href="{url.replace(chr(34), "%22")}">{label}</a>')
        return label

    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", _link, text)

    # 6. Bold then italic (order matters: ** before *)
    text = re.sub(r"\*\*(.+?)\*\*", lambda m: _hold(f"<b>{m.group(1)}</b>"), text)
    text = re.sub(r"__(.+?)__", lambda m: _hold(f"<b>{m.group(1)}</b>"), text)
    text = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", lambda m: _hold(f"<i>{m.group(1)}</i>"), text)
    text = re.sub(r"(?<![\w_])_([^_\n]+)_(?![\w_])", lambda m: _hold(f"<i>{m.group(1)}</i>"), text)

    # 7. Line-level: headers, checklists, bullets, hr
    lines = []
    for line in text.split("\n"):
        h = re.match(r"^#{1,6}\s+(.*)$", line)
        if h:
            lines.append(f"<b>{h.group(1).strip()}</b>")
            continue
        line = re.sub(r"^(\s*)[-*]\s+\[x\]\s+", r"\g<1>✅ ", line, flags=re.I)
        line = re.sub(r"^(\s*)[-*]\s+\[ \]\s+", r"\g<1>☐ ", line)
        line = re.sub(r"^(\s*)[-*]\s+", r"\g<1>• ", line)
        if re.match(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$", line):
            line = "───────"
        lines.append(line)
    text = "\n".join(lines)

    # 8. Restore stashed fragments
    text = re.sub(r"\x00(\d+)\x00", lambda m: stash[int(m.group(1))], text)
    return text.strip()


def strip_markdown(text: str) -> str:
    """Remove markdown so the text reads clean as plain text (fallback path)."""
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"_(.+?)_", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    text = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", text)
    text = re.sub(r"^- ", "", text, flags=re.MULTILINE)
    return text.strip()


def html_to_plain(html_text: str) -> str:
    """Strip HTML tags + unescape entities — used when Telegram rejects a chunk."""
    return _html.unescape(re.sub(r"</?[^>]+>", "", html_text)).strip()
```

(Chunking and senders come in Task 2 — same file.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_telegram_fmt.py -v`
Expected: all PASS. If an individual conversion test fails, fix the regex — do not weaken the test.

---

### Task 2: chunker + `send_html` / `post_html` senders

**Files:**
- Modify: `core/telegram_fmt.py` (append)
- Test: `tests/test_telegram_fmt.py` (append)

**Interfaces:**
- Consumes: `md_to_html`, `html_to_plain` from Task 1.
- Produces: `chunk_html(text: str, limit: int = 4000) -> list[str]`
- Produces: `async send_html(bot, chat_id, text, already_html=False, **kwargs) -> None` (PTB `bot.send_message` under the hood; raises nothing for parse errors — falls back; re-raises other errors)
- Produces: `post_html(token: str, chat_id: str, text: str, already_html=False, disable_web_page_preview=True, timeout=15) -> bool` (sync, `requests`, returns overall ok)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_telegram_fmt.py`:

```python
import asyncio
from core.telegram_fmt import chunk_html, send_html, post_html


# ── chunking ────────────────────────────────────────────────────────────
def test_chunk_short_text_single_chunk():
    assert chunk_html("hello") == ["hello"]

def test_chunk_splits_on_line_boundary():
    text = "\n".join(["x" * 100] * 50)  # 5049 chars
    chunks = chunk_html(text, limit=4000)
    assert len(chunks) == 2
    assert all(len(c) <= 4000 for c in chunks)
    assert "\n".join(chunks) == text

def test_chunk_reopens_pre_across_boundary():
    body = "\n".join(["line %d" % i for i in range(600)])
    text = f"<pre>{body}</pre>"
    chunks = chunk_html(text, limit=4000)
    assert len(chunks) >= 2
    for c in chunks:
        assert c.count("<pre") == c.count("</pre>")  # balanced per chunk

def test_chunk_hard_splits_giant_line():
    text = "y" * 9000  # no newlines at all
    chunks = chunk_html(text, limit=4000)
    assert all(len(c) <= 4000 for c in chunks)
    assert "".join(chunks) == text


# ── send_html (async, stub bot) ─────────────────────────────────────────
class StubBot:
    def __init__(self, fail_html=False):
        self.fail_html = fail_html
        self.sent = []  # (text, parse_mode)

    async def send_message(self, chat_id=None, text=None, parse_mode=None, **kw):
        if parse_mode == "HTML" and self.fail_html:
            raise Exception("Bad Request: can't parse entities: something")
        self.sent.append((text, parse_mode))


def test_send_html_converts_and_sends():
    bot = StubBot()
    asyncio.run(send_html(bot, 123, "**hi**"))
    assert bot.sent == [("<b>hi</b>", "HTML")]

def test_send_html_falls_back_to_plain_on_parse_error():
    bot = StubBot(fail_html=True)
    asyncio.run(send_html(bot, 123, "**hi**"))
    assert len(bot.sent) == 1
    text, mode = bot.sent[0]
    assert mode is None and text == "hi"

def test_send_html_already_html_passthrough():
    bot = StubBot()
    asyncio.run(send_html(bot, 123, "<b>x</b>", already_html=True))
    assert bot.sent == [("<b>x</b>", "HTML")]


# ── post_html (sync, monkeypatched requests) ────────────────────────────
def test_post_html_sends_html_then_falls_back(monkeypatch):
    calls = []

    class Resp:
        def __init__(self, ok):
            self.ok = ok

    def fake_post(url, json=None, timeout=None):
        calls.append(json)
        return Resp(json.get("parse_mode") != "HTML")  # reject HTML, accept plain

    import core.telegram_fmt as tf
    monkeypatch.setattr(tf.requests, "post", fake_post)
    monkeypatch.setattr(tf.time, "sleep", lambda s: None)
    ok = post_html("TOK", "123", "**hi**")
    assert ok is True
    assert calls[0]["parse_mode"] == "HTML" and calls[0]["text"] == "<b>hi</b>"
    assert "parse_mode" not in calls[1] and calls[1]["text"] == "hi"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_telegram_fmt.py -v`
Expected: new tests FAIL with `ImportError: cannot import name 'chunk_html'`

- [ ] **Step 3: Write the implementation**

Append to `core/telegram_fmt.py` (add `import requests` to the imports at top):

```python
def chunk_html(text: str, limit: int = MAX_LEN) -> list[str]:
    """Split on line boundaries at <= limit chars; keep <pre> balanced per chunk."""
    if len(text) <= limit:
        return [text]
    chunks, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:  # pathological single line — hard split
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(line[:limit])
            line = line[limit:]
        cand = (cur + "\n" + line) if cur else line
        if len(cand) > limit:
            chunks.append(cur)
            cur = line
        else:
            cur = cand
    if cur:
        chunks.append(cur)
    # Re-balance <pre> across chunk boundaries
    out, carry = [], False
    for c in chunks:
        if carry:
            c = "<pre>" + c
        open_pre = len(re.findall(r"<pre\b", c)) - c.count("</pre>")
        if open_pre > 0:
            c += "</pre>"
            carry = True
        else:
            carry = False
        out.append(c)
    return out


def _is_parse_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "parse entities" in msg or "can't parse" in msg or "unsupported start tag" in msg


async def send_html(bot, chat_id, text, already_html: bool = False, **kwargs):
    """Async sender for PTB bots: convert, chunk, send HTML, fall back to plain."""
    html_text = text if already_html else md_to_html(text)
    chunks = chunk_html(html_text)
    for i, chunk in enumerate(chunks):
        try:
            await bot.send_message(chat_id=chat_id, text=chunk, parse_mode="HTML", **kwargs)
        except Exception as e:
            if not _is_parse_error(e):
                raise
            logger.warning("telegram_fmt: HTML parse rejected, plain fallback: %.120s", chunk)
            await bot.send_message(chat_id=chat_id, text=html_to_plain(chunk), **kwargs)
        if i < len(chunks) - 1:
            await asyncio.sleep(0.3)


def post_html(token: str, chat_id, text: str, already_html: bool = False,
              disable_web_page_preview: bool = True, timeout: int = 15) -> bool:
    """Sync sender for cron scripts / skills: convert, chunk, send, fall back."""
    if not token or not chat_id:
        logger.warning("telegram_fmt.post_html: missing token/chat_id")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    html_text = text if already_html else md_to_html(text)
    chunks = chunk_html(html_text)
    ok_all = True
    for i, chunk in enumerate(chunks):
        try:
            r = requests.post(url, json={
                "chat_id": chat_id, "text": chunk, "parse_mode": "HTML",
                "disable_web_page_preview": disable_web_page_preview,
            }, timeout=timeout)
            if not r.ok:
                logger.warning("telegram_fmt: HTML send failed (%s), plain fallback", r.status_code)
                r = requests.post(url, json={
                    "chat_id": chat_id, "text": html_to_plain(chunk),
                    "disable_web_page_preview": disable_web_page_preview,
                }, timeout=timeout)
            ok_all = ok_all and r.ok
        except Exception as e:
            logger.error("telegram_fmt.post_html error: %s", e)
            ok_all = False
        if i < len(chunks) - 1:
            time.sleep(0.3)
    return ok_all
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_telegram_fmt.py -v`
Expected: all PASS (Tasks 1+2 tests together).

---

### Task 3: wire `BaseAgent._send_response` (all 8 agents' replies)

**Files:**
- Modify: `core/base_agent.py` — `_strip_markdown` (~line 1884) and `_send_response` (~line 1902)
- Test: `tests/test_telegram_fmt.py` (append one integration-ish test)

**Interfaces:**
- Consumes: `send_html`, `strip_markdown` from `core.telegram_fmt`.
- Produces: unchanged signature `async _send_response(self, bot, chat_id, text)` — callers (all agent files) untouched.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_telegram_fmt.py`:

```python
def test_base_agent_no_longer_strips_markdown():
    """_send_response must route through telegram_fmt, not the old stripper."""
    import inspect
    from core import base_agent
    src = inspect.getsource(base_agent.BaseAgent._send_response)
    assert "send_html" in src, "_send_response should call telegram_fmt.send_html"
    assert "_strip_markdown(text)" not in src, "old stripper still in send path"
```

Run: `venv/bin/pytest tests/test_telegram_fmt.py::test_base_agent_no_longer_strips_markdown -v`
Expected: FAIL (send path still uses `_strip_markdown`).
Note: if importing `core.base_agent` fails in the test env for dependency reasons, replace the test with a source-text check via `Path("core/base_agent.py").read_text()` — assert the same two things within the `_send_response` function body.

- [ ] **Step 2: Modify `core/base_agent.py`**

(a) Replace the `_strip_markdown` static method body (~line 1884) with a delegation, keeping the method for any external callers:

```python
    @staticmethod
    def _strip_markdown(text: str) -> str:
        """Plain-text fallback — delegated to core.telegram_fmt."""
        from core.telegram_fmt import strip_markdown
        return strip_markdown(text)
```

(b) In `_send_response` (~line 1902), keep everything through the claim-verifier / auto-DISPATCH block unchanged. Then replace the tail — from `text = self._strip_markdown(text)` through the end of the chunk-send loop — with:

```python
        from core.telegram_fmt import send_html
        await send_html(bot, chat_id, text)
```

Delete the now-dead local `MAX_LEN`/`parts` chunking code in that method (chunking lives in `telegram_fmt`).

- [ ] **Step 3: Run tests + import check**

Run: `venv/bin/pytest tests/test_telegram_fmt.py -v`
Expected: all PASS.
Run: `venv/bin/python -c "import core.base_agent; print('ok')"`
Expected: `ok`

---

### Task 4: wire `task_runner` notifications + `cron_helpers`

**Files:**
- Modify: `core/task_runner.py` — `notify_serge` (lines ~447-456), `notify_agent` (lines ~812-835)
- Modify: `agents/cron_helpers.py` — `send_telegram` (lines ~66-82)

**Interfaces:**
- Consumes: `post_html(token, chat_id, text, ...)` from Task 2.
- Produces: unchanged signatures — `notify_serge(message)`, `notify_agent(agent_id, message)`, `cron_helpers.send_telegram(message, token=None, chat_id=None)`.

- [ ] **Step 1: Replace `notify_serge` body in `core/task_runner.py`**

```python
def notify_serge(message: str):
    if not TELEGRAM_TOKEN:
        logger.warning("No Telegram token — skipping notify")
        return
    try:
        from core.telegram_fmt import post_html
        post_html(TELEGRAM_TOKEN, SERGE_CHAT_ID, message)
    except Exception as e:
        logger.error(f"Telegram notify error: {e}")
```

- [ ] **Step 2: In `notify_agent` (same file, ~line 812)**, keep the `token_env_map` lookup exactly as-is; replace only the `requests.post(...)` send call with:

```python
        from core.telegram_fmt import post_html
        post_html(token, SERGE_CHAT_ID, message)
```

(Preserve the function's existing guard clauses and try/except structure.)

- [ ] **Step 3: Replace the send body of `cron_helpers.send_telegram`** (keep the signature and the `tok`/`cid` resolution + guard):

```python
def send_telegram(message: str, token: str = None, chat_id: str = None):
    """Send a Telegram message to Serge (markdown → rich HTML, auto-chunked)."""
    tok = token or TELEGRAM_TOKEN
    cid = chat_id or SERGE_CHAT_ID
    if not tok or not cid:
        log.warning("No Telegram token/chat_id configured")
        return
    try:
        from core.telegram_fmt import post_html
        post_html(tok, cid, message)
    except Exception as e:
        log.error(f"Telegram send failed: {e}")
```

Note: callers of `cron_helpers.send_telegram` today already write `<b>`-style HTML (it sent with parse_mode=HTML). `md_to_html`'s pass-through guard keeps pure-HTML messages untouched, so this is safe for both HTML-writing and markdown-writing callers. Remove the old truncate-at-4000 logic — chunking replaces it.

- [ ] **Step 4: Import checks**

Run: `venv/bin/python -c "import core.task_runner, agents.cron_helpers; print('ok')"`
Expected: `ok`
Run: `venv/bin/pytest tests/test_telegram_fmt.py -v` (regression)
Expected: all PASS.

---

### Task 5: wire digest scripts + shared skills + commander/approval chunking

**Files:**
- Modify: `scripts/duke_morning_digest.py` — `send_telegram` (lines ~85-110)
- Modify: `agents/simon_bately/briefing_cron.py` — `send_telegram` (lines ~334-351)
- Modify: `scripts/hallucination_weekly_digest.py` — `send_telegram` (lines ~138-160)
- Modify: `skills/shared/send_telegram.py`
- Modify: `skills/shared/infra_report.py` — `send_telegram` (lines ~90-103)
- Modify: `skills/shared/research_report.py` — send block (lines ~52-66)
- Modify: `skills/shared/suggest_action.py` — `tg_send` (lines ~69-80)
- Modify: `core/commander.py` — sends at lines ~186-202 and ~307-337
- Modify: `core/approval.py` — `_send_telegram` (lines ~59-74)

**Interfaces:**
- Consumes: `post_html`, `md_to_html`, `chunk_html`, `html_to_plain` from Task 2. All function signatures in these files stay unchanged.

All these scripts add repo root to `sys.path` already (they import framework modules) — where one doesn't, add at top:

```python
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))  # adjust depth to reach repo root
```

- [ ] **Step 1: `scripts/duke_morning_digest.py`** — replace `send_telegram` body (keep signature + missing-env print fallback):

```python
def send_telegram(text: str) -> bool:
    if not DUKE_TOKEN or not SERGE_CHAT_ID:
        print("[duke_digest] missing TELEGRAM_DUKE_HARMON or SERGE_CHAT_ID — printing instead:")
        print(text)
        return False
    from core.telegram_fmt import post_html
    return post_html(DUKE_TOKEN, SERGE_CHAT_ID, text)
```

- [ ] **Step 2: `agents/simon_bately/briefing_cron.py`** — replace `send_telegram` (drop the `strip_markdown(text)` call at line ~335; the briefing's markdown now renders instead of being stripped):

```python
def send_telegram(text: str):
    from core.telegram_fmt import post_html
    ok = post_html(TELEGRAM_TOKEN, SERGE_CHAT_ID, text)
    if not ok:
        print("[briefing] telegram send failed")
```

If the local `strip_markdown()` helper in this file has no other callers after this change, delete it.

- [ ] **Step 3: `scripts/hallucination_weekly_digest.py`** — same pattern:

```python
def send_telegram(text: str) -> bool:
    if not SIMON_TOKEN or not SERGE_CHAT:
        print("[hallucination_digest] missing Telegram env — printing instead:")
        print(text)
        return False
    from core.telegram_fmt import post_html
    return post_html(SIMON_TOKEN, SERGE_CHAT, text)
```

- [ ] **Step 4: `skills/shared/send_telegram.py`** — route through the formatter (fixes its latent no-escaping parse-error bug):

```python
#!/usr/bin/env python3
"""Send a message to a Telegram chat via bot API (markdown → rich HTML)."""
import os, sys, json
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
token = args.get("token", os.environ.get("TELEGRAM_SIMON_BATELY", ""))
chat_id = args.get("chat_id", "")
text = args.get("text", "")
if not text:
    print(json.dumps({"error": "text required"}))
else:
    try:
        from core.telegram_fmt import post_html
        ok = post_html(token, chat_id, text)
        print(json.dumps({"success": bool(ok)}))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
```

(The old version returned `message_id`; grep callers first — `grep -rn "message_id" skills/ agents/ core/ --include="*.py" | grep -i send_telegram`. If anything consumes it, keep a `message_id: null` key in the success payload.)

- [ ] **Step 5: `skills/shared/infra_report.py` and `skills/shared/suggest_action.py`** — these already build HTML by hand and send with parse_mode=HTML. Keep their message-building untouched; replace only the raw urllib POST with:

```python
        from core.telegram_fmt import post_html
        return post_html(TELEGRAM_TOKEN, SERGE_CHAT_ID, msg, already_html=True)
```

(`suggest_action.tg_send`: same, using its `BOT_TOKEN`/`SERGE_CHAT_ID`, return a `{"ok": bool}` dict to match its current return shape. This buys them chunking + fallback without double-escaping.)

- [ ] **Step 6: `skills/shared/research_report.py`** — replace the urllib send (lines ~52-66 inner block) with:

```python
        from core.telegram_fmt import post_html
        if token and chat_id:
            msg = (f"📋 **Research Report: {topic}**\nBy: {agent_id}\n\n{findings[:500]}\n\n"
                   f"💬 Was this sufficient? Reply with feedback or 'ok'.")
            post_html(token, chat_id, msg)
```

- [ ] **Step 7: `core/commander.py`** — both sends (~line 197 and ~line 332) already build HTML-ish text but have NO chunking and NO fallback; `[TASK:...]` texts contain `<your full report>` style angle brackets that can 400. Replace each `client.post(... sendMessage ...)` with a loop over `chunk_html` and plain-text retry, e.g. for the dispatch send:

```python
            from core.telegram_fmt import chunk_html, html_to_plain
            async with httpx.AsyncClient(timeout=15) as client:
                for chunk in chunk_html(message):
                    resp = await client.post(
                        TELEGRAM_API.format(token=token, method="sendMessage"),
                        json={"chat_id": chat_id, "text": chunk, "parse_mode": "HTML"},
                    )
                    if resp.status_code == 400:
                        resp = await client.post(
                            TELEGRAM_API.format(token=token, method="sendMessage"),
                            json={"chat_id": chat_id, "text": html_to_plain(chunk)},
                        )
```

Apply the same wrap to `_send_final_report` (~line 332) with `self.simon_token` / `self.serge_chat_id`. Preserve each site's surrounding try/except and any use of `resp` afterwards.

- [ ] **Step 8: `core/approval.py._send_telegram`** — already HTML via urllib; replace the body with the shared sender, preserving its dict return shape:

```python
def _send_telegram(token: str, chat_id: str, text: str) -> dict:
    if not token or not chat_id:
        return {"ok": False, "error": "missing token or chat_id"}
    from core.telegram_fmt import post_html
    return {"ok": post_html(token, chat_id, text, already_html=True)}
```

(Check callers of `_send_telegram` first: if any reads `result["result"]["message_id"]`, keep the raw urllib call for the FIRST chunk and use post_html only for overflow — otherwise this simple form is fine.)

- [ ] **Step 9: Import checks + full test run**

```bash
venv/bin/python -c "import core.commander, core.approval; print('ok')"
venv/bin/python -m py_compile scripts/duke_morning_digest.py agents/simon_bately/briefing_cron.py scripts/hallucination_weekly_digest.py skills/shared/send_telegram.py skills/shared/infra_report.py skills/shared/research_report.py skills/shared/suggest_action.py && echo ok
venv/bin/pytest tests/test_telegram_fmt.py -v
```
Expected: `ok`, `ok`, all tests PASS.

---

### Task 6: house-style block in the shared system prompt

**Files:**
- Modify: `core/context_mixin.py` — `get_system_prompt` (~lines 125-143)
- Test: `tests/test_telegram_fmt.py` (append)

**Interfaces:**
- Consumes: nothing new. Produces: every agent's system prompt ends with the style block (both legacy and scaffold paths — they all resolve personas through `get_system_prompt`).

- [ ] **Step 1: Write the failing test**

```python
def test_house_style_in_context_mixin_source():
    src = (ROOT / "core" / "context_mixin.py").read_text()
    assert "TELEGRAM_STYLE" in src
    assert "✅" in src and "☐" in src
```

Run: `venv/bin/pytest tests/test_telegram_fmt.py::test_house_style_in_context_mixin_source -v`
Expected: FAIL.

- [ ] **Step 2: Add to `core/context_mixin.py`**

Module-level constant (near the top, after imports):

```python
TELEGRAM_STYLE = """
## Telegram formatting (house style)
Your replies render in Telegram with rich text. Write normal markdown — it is converted automatically.
- Simple answers: 1-3 plain sentences. Do NOT force structure onto chit-chat.
- Structured answers: start with one short **bold** header line.
- Status marks: ✅ done · ⚠️ needs attention · ❌ failed · ☐ todo.
- Use "- " bullets for lists and "- [x] / - [ ]" checklists for multi-step work.
- Put file paths, commands, and service names in `backticks`.
- No tables, no nested headers. Keep messages compact.
"""
```

In `get_system_prompt`, append `TELEGRAM_STYLE` to the returned prompt string at the end (after the `<context>` wrapping, whatever the current final return expression is — e.g. `return prompt + TELEGRAM_STYLE`). Apply to ALL return paths of the function (persona-file path and both fallbacks).

- [ ] **Step 3: Verify**

```bash
venv/bin/pytest tests/test_telegram_fmt.py -v
venv/bin/python -c "import core.context_mixin; print('ok')"
```
Expected: PASS + `ok`. (Prompt cache TTL is 120s — restarts in Task 7 make it immediate.)

---

### Task 7: deploy — restart services + live smoke test

**Files:** none (operational).

- [ ] **Step 1: Full regression** — run the framework's related suites:

```bash
venv/bin/pytest tests/test_telegram_fmt.py tests/test_new_gap_skills.py -v
```
Expected: all PASS. Also run any existing test files that reference `_send_response`/`strip_markdown` (`grep -rln "_send_response\|strip_markdown" tests/`) and make them pass.

- [ ] **Step 2: Restart all agent services** (exact unit names from the system — do not guess):

```bash
systemctl list-units 'baza-agent-*' --no-legend | awk '{print $1}'
sudo systemctl restart <each unit listed>
systemctl is-active 'baza-agent-*'
```
Expected: every unit `active`. Check logs for a clean start: `journalctl -u <one unit> -n 20 --no-pager`.

- [ ] **Step 3: Live smoke test** — send a formatted message through the real path (uses Simon's token from `configs/secrets.env`):

```bash
venv/bin/python - <<'EOF'
import os
from dotenv import load_dotenv
load_dotenv("configs/secrets.env")
from core.telegram_fmt import post_html
msg = """### Rich text is live ✅
**What changed:** agent replies now render formatting.
- [x] converter + chunking + fallback
- [x] all 8 agents rewired
- [ ] Specter on phantom (later, via rsync)
Try asking any agent for a status report — paths appear like `core/telegram_fmt.py`."""
ok = post_html(os.environ["TELEGRAM_SIMON_BATELY"], os.environ["SERGE_CHAT_ID"], msg)
print("sent:", ok)
EOF
```
Expected: `sent: True` and a nicely formatted message in Serge's Telegram (bold header, ✅/☐ checklist, `code` path).

- [ ] **Step 4: Watch one real agent reply** — message an agent (e.g. Phil) "give me a quick status checklist" and confirm the reply renders bold/bullets/checks. Check `journalctl -u baza-agent-phil-hass.service -f` (exact unit name from Step 2) for `telegram_fmt` fallback warnings — there should be none.

- [ ] **Step 5: Log + wrap up** — append session-log entry (timestamped from `date '+%Y-%m-%d %H:%M'`) summarizing files touched, tests, restarts. No manual git commit (auto-git timer).
