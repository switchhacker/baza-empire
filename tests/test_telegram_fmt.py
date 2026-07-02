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

def test_unsafe_html_with_allowed_tag_is_escaped():
    out = md_to_html("<script>alert(1)</script><b>ok</b>")
    assert "<script>" not in out and "&lt;script&gt;" in out

def test_bare_angle_bracket_with_tag_is_escaped():
    out = md_to_html("<b>Report</b> a < b")
    assert "&lt;" in out and "<b>Report</b>" not in out  # falls through to escape path

def test_safe_html_still_passes_through():
    src = '<b>Done:</b> restarted <code>baza-dashboard</code> &amp; more'
    assert md_to_html(src) == src

# ── pass-through attribute allowlist (security) ──────────────────────────
def test_passthrough_rejects_event_handler_attribute():
    out = md_to_html('<b onclick="steal()">click me</b>')
    assert "onclick" not in out or "&lt;" in out  # must be escaped, not passed through

def test_passthrough_rejects_javascript_href():
    out = md_to_html('<a href="javascript:alert(1)">click</a><b>ok</b>')
    assert '<a href="javascript:' not in out

def test_passthrough_allows_https_href():
    src = '<a href="https://ahb123.com">site</a> <b>ok</b>'
    assert md_to_html(src) == src

def test_passthrough_allows_language_code_class():
    src = '<pre><code class="language-python">print(1)</code></pre>'
    assert md_to_html(src) == src

# ── nested emphasis / sentinel safety ────────────────────────────────────
def test_nested_emphasis_no_nul_leak():
    out = md_to_html("*italic **bold** end*")
    assert "\x00" not in out
    assert out == "<i>italic <b>bold</b> end</i>"

def test_literal_nul_input_stripped():
    out = md_to_html("\x000\x00**bold**")
    assert "\x00" not in out and "<b>bold</b>" in out

# ── helpers ─────────────────────────────────────────────────────────────
def test_strip_markdown():
    assert strip_markdown("### H\n**b** `c` [t](http://x)\n- item") == "H\nb c t\nitem"

def test_html_to_plain():
    assert html_to_plain("<b>a &amp; b</b>\n<pre>c</pre>") == "a & b\nc"


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
