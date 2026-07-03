"""Tests for Task 17 of the cron-improvements plan: Telegram inline buttons
(✓ Ack / 😴 24h / ➕ Task) on cron alerts.

Covers three pieces:
  - core.telegram_fmt.post_html's new `reply_markup` kwarg -- attached to
    the FINAL chunk's sendMessage payload only, on both the HTML attempt and
    its plain-text fallback (tests/test_telegram_fmt.py's 49 tests cover the
    rest of that module and must stay green untouched).
  - agents.cron_helpers.send_alert building the inline_keyboard payload from
    should_alert's row_id, and routing buttoned alerts through Duke's bot
    token (core/base_agent.py's architecture) rather than Simon's legacy
    core/agent.py token, since only a BaseAgent-backed bot can answer a
    Telegram callback_query.
  - core.base_agent.BaseAgent._on_cron_callback: parses "cron|<action>|<id>",
    dispatches ack/snooze/task, edits the alert message, always answers the
    callback query, and fails safe (query.answer("failed: ...")) on bad
    input or a DB error -- never raising out of the handler.
"""
import asyncio
import importlib
import os
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)


# ── fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture()
def chdb(monkeypatch):
    """Fresh core.cron_health_db against a tmp DB (mirrors the fixture
    pattern in tests/test_cron_helpers_routing.py) -- used directly by the
    _on_cron_callback tests, and indirectly by the `ch` fixture below."""
    tmpdir = tempfile.mkdtemp(prefix="alert_buttons_")
    path = os.path.join(tmpdir, "cron_health.db")
    monkeypatch.setenv("BAZA_CRON_HEALTH_DB", path)
    if "core.cron_health_db" in sys.modules:
        del sys.modules["core.cron_health_db"]
    mod = importlib.import_module("core.cron_health_db")
    mod.init()
    return mod


@pytest.fixture()
def ch(monkeypatch, chdb):
    """Fresh agents.cron_helpers pointed at the tmp cron_health DB, with
    TELEGRAM_DUKE_HARMON pinned to a known fake token so routing assertions
    are deterministic regardless of what's actually in configs/secrets.env."""
    monkeypatch.setenv("TELEGRAM_DUKE_HARMON", "duke-fake-token")
    if "agents.cron_helpers" in sys.modules:
        del sys.modules["agents.cron_helpers"]
    return importlib.import_module("agents.cron_helpers")


class FakeTaskManager:
    """Stands in for core.task_updater.AgentTaskManager -- records calls
    instead of writing into the real dashboard/baza_projects.db (which is
    live production data on this host, not a test fixture)."""

    def __init__(self):
        self.calls = []

    def add(self, project_id, title, **kwargs):
        self.calls.append({"project_id": project_id, "title": title, **kwargs})
        return "fake-task-id-123"


class FakeAgent:
    """Minimal stand-in for a BaseAgent instance -- _on_cron_callback only
    touches self.AGENT_ID and self.tasks."""
    AGENT_ID = "duke_harmon"

    def __init__(self):
        self.tasks = FakeTaskManager()


def _make_query(callback_data, message_text="Disk full on /home"):
    query = SimpleNamespace()
    query.data = callback_data
    query.message = SimpleNamespace(text=message_text)
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    return query


def _make_update(query):
    return SimpleNamespace(callback_query=query)


def _answer_text(mock_call):
    args, kwargs = mock_call
    return args[0] if args else kwargs.get("text", "")


# ── post_html reply_markup placement ────────────────────────────────────

def test_post_html_reply_markup_last_chunk_only(monkeypatch):
    from core import telegram_fmt as tf

    calls = []

    class Resp:
        ok = True

    def fake_post(url, json=None, timeout=None):
        calls.append(json)
        return Resp()

    monkeypatch.setattr(tf.requests, "post", fake_post)
    monkeypatch.setattr(tf.time, "sleep", lambda s: None)

    long_text = "\n".join(["x" * 100] * 50)  # 5049 chars, MAX_LEN=4000 -> 2 chunks
    kb = {"inline_keyboard": [[{"text": "✓ Ack", "callback_data": "cron|ack|1"}]]}
    ok = tf.post_html("TOK", "123", long_text, reply_markup=kb)

    assert ok is True
    assert len(calls) >= 2
    for c in calls[:-1]:
        assert "reply_markup" not in c
    assert calls[-1]["reply_markup"] == kb


def test_post_html_reply_markup_on_plain_fallback_too(monkeypatch):
    """reply_markup must also land on the plain-text fallback payload when
    the HTML attempt is rejected -- not only on a successful HTML send."""
    from core import telegram_fmt as tf

    calls = []

    class Resp:
        def __init__(self, ok):
            self.ok = ok

    def fake_post(url, json=None, timeout=None):
        calls.append(json)
        return Resp(json.get("parse_mode") != "HTML")  # reject HTML, accept plain

    monkeypatch.setattr(tf.requests, "post", fake_post)
    monkeypatch.setattr(tf.time, "sleep", lambda s: None)

    kb = {"inline_keyboard": [[{"text": "✓ Ack", "callback_data": "cron|ack|1"}]]}
    ok = tf.post_html("TOK", "123", "**hi**", reply_markup=kb)

    assert ok is True
    assert len(calls) == 2
    assert "reply_markup" not in calls[0] or calls[0]["parse_mode"] == "HTML"
    assert "parse_mode" not in calls[1]
    assert calls[1]["reply_markup"] == kb


def test_post_html_no_reply_markup_when_omitted(monkeypatch):
    """Default (no reply_markup arg) must not add the key at all -- existing
    callers (e.g. send_report) shouldn't start sending an empty keyboard."""
    from core import telegram_fmt as tf

    calls = []

    class Resp:
        ok = True

    def fake_post(url, json=None, timeout=None):
        calls.append(json)
        return Resp()

    monkeypatch.setattr(tf.requests, "post", fake_post)
    tf.post_html("TOK", "123", "hi")
    assert "reply_markup" not in calls[0]


# ── send_alert() button payload + Duke routing ──────────────────────────

def test_send_alert_button_payload(ch, monkeypatch):
    captured = {}

    def fake_post_html(token, chat_id, text, *args, **kwargs):
        captured["token"] = token
        captured["chat_id"] = chat_id
        captured["reply_markup"] = kwargs.get("reply_markup")
        return True

    import core.telegram_fmt as telegram_fmt
    monkeypatch.setattr(telegram_fmt, "post_html", fake_post_html)

    ok = ch.send_alert("cronX", "Disk full on /home\nmore detail", alert_key="disk_full_btn")
    assert ok is True

    with ch._chdb().connect() as conn:
        row = conn.execute(
            "SELECT id FROM cron_alert_state WHERE key = ?", ("disk_full_btn",)
        ).fetchone()
    row_id = row["id"]

    assert captured["reply_markup"] == {
        "inline_keyboard": [[
            {"text": "✓ Ack", "callback_data": f"cron|ack|{row_id}"},
            {"text": "😴 24h", "callback_data": f"cron|snooze|{row_id}"},
            {"text": "➕ Task", "callback_data": f"cron|task|{row_id}"},
        ]]
    }
    # buttons=True (default) + no explicit token -> routed through Duke, not Simon.
    assert captured["token"] == "duke-fake-token"


def test_send_alert_buttons_false_no_markup_default_token(ch, monkeypatch):
    captured = {}

    def fake_post_html(token, chat_id, text, *args, **kwargs):
        captured["token"] = token
        captured["reply_markup"] = kwargs.get("reply_markup")
        return True

    import core.telegram_fmt as telegram_fmt
    monkeypatch.setattr(telegram_fmt, "post_html", fake_post_html)

    ch.send_alert("cronY", "no buttons here", alert_key="no_buttons_key", buttons=False)

    assert captured["reply_markup"] is None
    assert captured["token"] == ch.TELEGRAM_TOKEN  # Simon's default, unchanged


def test_send_alert_explicit_token_overrides_duke_routing(ch, monkeypatch):
    captured = {}

    def fake_post_html(token, chat_id, text, *args, **kwargs):
        captured["token"] = token
        return True

    import core.telegram_fmt as telegram_fmt
    monkeypatch.setattr(telegram_fmt, "post_html", fake_post_html)

    ch.send_alert("cronZ", "explicit token", alert_key="explicit_token_key",
                 token="some-other-token")
    assert captured["token"] == "some-other-token"


# ── BaseAgent._on_cron_callback ──────────────────────────────────────────

def test_callback_ack_updates_state(chdb):
    from core import base_agent

    _, row_id = chdb.should_alert("ack_test_key", None, {"title": "Disk full on /home"})
    agent = FakeAgent()
    query = _make_query(f"cron|ack|{row_id}", message_text="Disk full on /home")
    update = _make_update(query)

    asyncio.run(base_agent.BaseAgent._on_cron_callback(agent, update, None))

    row = chdb.alert_get(row_id)
    assert row["acked_at"] is not None

    query.edit_message_text.assert_awaited_once_with("Disk full on /home\n\n✓ acknowledged")
    query.answer.assert_awaited_once()
    assert not _answer_text(query.answer.call_args).startswith("failed")


def test_callback_snooze_sets_snoozed_until(chdb):
    from core import base_agent

    _, row_id = chdb.should_alert("snooze_test_key", None, {"title": "GPU hot"})
    agent = FakeAgent()
    query = _make_query(f"cron|snooze|{row_id}", message_text="GPU hot")
    update = _make_update(query)

    asyncio.run(base_agent.BaseAgent._on_cron_callback(agent, update, None))

    row = chdb.alert_get(row_id)
    assert row["snoozed_until"] is not None
    query.edit_message_text.assert_awaited_once_with("GPU hot\n\n😴 snoozed 24h")
    query.answer.assert_awaited_once_with("😴 snoozed 24h")


def test_callback_task_inserts_row(chdb):
    from core import base_agent

    _, row_id = chdb.should_alert("task_test_key", None, {"title": "GPU overheating"})
    agent = FakeAgent()
    query = _make_query(f"cron|task|{row_id}", message_text="GPU overheating\nmore detail")
    update = _make_update(query)

    asyncio.run(base_agent.BaseAgent._on_cron_callback(agent, update, None))

    assert len(agent.tasks.calls) == 1
    call = agent.tasks.calls[0]
    assert call["title"] == "GPU overheating"  # from alert meta, not the raw message text
    assert call["project_id"] == "shared"

    query.edit_message_text.assert_awaited_once_with(
        "GPU overheating\nmore detail\n\n➕ task created"
    )
    query.answer.assert_awaited_once_with("➕ task created")


def test_callback_task_falls_back_to_generic_title_when_meta_missing(chdb):
    """meta may be absent/unparseable -- title must degrade gracefully
    instead of raising."""
    from core import base_agent

    with chdb.connect() as conn:
        conn.execute(
            "INSERT INTO cron_alert_state (key, first_seen, last_seen, meta) "
            "VALUES ('no_meta_key', '2026-01-01T00:00:00', '2026-01-01T00:00:00', NULL)"
        )
        conn.commit()
        row_id = conn.execute(
            "SELECT id FROM cron_alert_state WHERE key='no_meta_key'"
        ).fetchone()["id"]

    agent = FakeAgent()
    query = _make_query(f"cron|task|{row_id}")
    update = _make_update(query)

    asyncio.run(base_agent.BaseAgent._on_cron_callback(agent, update, None))

    assert agent.tasks.calls[0]["title"] == "cron alert"


def test_callback_bad_id_answers_error(chdb):
    from core import base_agent

    agent = FakeAgent()
    query = _make_query("cron|ack|not-a-number")
    update = _make_update(query)

    asyncio.run(base_agent.BaseAgent._on_cron_callback(agent, update, None))

    query.answer.assert_awaited_once()
    assert _answer_text(query.answer.call_args).startswith("failed:")
    query.edit_message_text.assert_not_awaited()


def test_callback_unknown_action_answers_error(chdb):
    from core import base_agent

    agent = FakeAgent()
    query = _make_query("cron|nuke|1")
    update = _make_update(query)

    asyncio.run(base_agent.BaseAgent._on_cron_callback(agent, update, None))

    query.answer.assert_awaited_once()
    assert _answer_text(query.answer.call_args).startswith("failed:")
    query.edit_message_text.assert_not_awaited()


def test_callback_db_error_answers_failed_without_raising(chdb, monkeypatch):
    """A DB-layer exception (e.g. locked file) must never escape the handler
    -- it degrades to query.answer('failed: ...')."""
    from core import base_agent

    _, row_id = chdb.should_alert("db_err_key", None, {"title": "x"})
    monkeypatch.setattr(
        chdb, "alert_ack",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db locked")),
    )

    agent = FakeAgent()
    query = _make_query(f"cron|ack|{row_id}")
    update = _make_update(query)

    asyncio.run(base_agent.BaseAgent._on_cron_callback(agent, update, None))  # must not raise

    query.answer.assert_awaited_once()
    assert _answer_text(query.answer.call_args).startswith("failed:")
    assert "db locked" in _answer_text(query.answer.call_args)
    query.edit_message_text.assert_not_awaited()


def test_callback_no_callback_query_is_noop():
    """update.callback_query is None for non-callback updates routed here by
    mistake -- must return quietly, not raise."""
    from core import base_agent

    agent = FakeAgent()
    update = SimpleNamespace(callback_query=None)
    asyncio.run(base_agent.BaseAgent._on_cron_callback(agent, update, None))  # must not raise
