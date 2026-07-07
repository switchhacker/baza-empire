"""Tests for voice_flow.agent_client against the REAL Fluid contract.

Verified contract (see docs/superpowers/plans/2026-07-07-baza-flow-fixes.md):
- Session is minted by GET /fluid; the id is embedded in the HTML as
  <body class="state-idle" data-session-id="...">  (fluid.html line 16).
- POST /api/fluid/say {session_id, text, agent_id}; 404 if session unknown.
- GET /api/fluid/stream?session_id= is an infinite SSE stream:
  id: N / event: <type> / data: <JSON OBJECT> frames, ": keep-alive" comments.
- Reply sentences arrive one per `agent_token` event in payload["text"];
  the turn ends with `agent_turn_end`.
"""
import pytest

from voice_flow.agent_client import AgentClient, AgentReply

SESSION = "u7Yx9_ab-CDef012"  # secrets.token_urlsafe(12)-shaped
HTML = (
    "<!doctype html>\n<html>\n<head><title>Fluid</title></head>\n"
    f'<body class="state-idle" data-session-id="{SESSION}">\n'
    '<div id="orb"></div>\n</body>\n</html>'
)

# Real SSE frame shapes: data is always a JSON object; one sentence per token.
REAL_FRAMES = [
    b"id: 1",
    b"event: agent_token",
    b'data: {"agent_id": "specter_voss", "sentence_id": 1, "ordinal": 0, '
    b'"text": "Hello Serge.", "spoken": true}',
    b"",
    b": keep-alive",
    b"",
    b"id: 2",
    b"event: agent_token",
    b'data: {"agent_id": "specter_voss", "sentence_id": 2, "ordinal": 1, '
    b'"text": "How can I help?", "spoken": true}',
    b"",
    b"id: 3",
    b"event: agent_turn_end",
    b'data: {"agent_id": "specter_voss"}',
    b"",
]


class FakeResp:
    def __init__(self, status_code=200, text="", lines=None):
        self.status_code = status_code
        self.text = text
        self._lines = lines if lines is not None else []
        self.closed = False

    def iter_lines(self):
        return iter(self._lines)

    def close(self):
        self.closed = True


class FakeHttp:
    """Routes by URL: /fluid -> HTML page, /api/fluid/stream -> SSE lines."""

    def __init__(self, html=HTML, say_statuses=None, stream_lines=None):
        self.html = html
        self._say_statuses = list(say_statuses or [])
        self._stream_lines = stream_lines if stream_lines is not None else REAL_FRAMES
        self.gets = []
        self.posts = []

    def get(self, url, **kw):
        self.gets.append((url, kw))
        if url.endswith("/fluid"):
            return FakeResp(text=self.html)
        return FakeResp(lines=self._stream_lines)

    def post(self, url, **kw):
        self.posts.append((url, kw))
        status = self._say_statuses.pop(0) if self._say_statuses else 200
        return FakeResp(status_code=status, text='{"ok": true}')

    # helpers
    def fluid_gets(self):
        return [g for g in self.gets if g[0].endswith("/fluid")]

    def stream_gets(self):
        return [g for g in self.gets if "/api/fluid/stream" in g[0]]

    def say_posts(self):
        return [p for p in self.posts if p[0].endswith("/api/fluid/say")]


def test_ensure_session_mints_from_html():
    http = FakeHttp()
    c = AgentClient("http://fluid", "specter_voss", http=http)
    sid = c._ensure_session()
    assert sid == SESSION
    # cached: a second call must not hit /fluid again
    assert c._ensure_session() == SESSION
    assert len(http.fluid_gets()) == 1


def test_ensure_session_unparseable_html_raises():
    http = FakeHttp(html="<html><body>no session here</body></html>")
    c = AgentClient("http://fluid", "specter_voss", http=http)
    with pytest.raises(RuntimeError, match="could not obtain Fluid session"):
        c._ensure_session()


def test_preset_session_id_skips_mint():
    http = FakeHttp()
    c = AgentClient("http://fluid", "specter_voss", http=http,
                    session_id="preset-sid-123")
    assert c._ensure_session() == "preset-sid-123"
    assert len(http.fluid_gets()) == 0


def test_ask_assembles_reply_from_real_frames():
    http = FakeHttp()
    c = AgentClient("http://fluid", "specter_voss", http=http)
    reply = c.ask("what's the status")
    assert isinstance(reply, AgentReply)
    assert reply.text == "Hello Serge. How can I help?"
    assert reply.agent_id == "specter_voss"


def test_ask_posts_say_with_minted_session():
    http = FakeHttp()
    c = AgentClient("http://fluid", "specter_voss", http=http)
    c.ask("what's the status")
    says = http.say_posts()
    assert len(says) == 1
    url, kw = says[0]
    assert url == "http://fluid/api/fluid/say"
    assert kw["json"] == {"session_id": SESSION,
                          "text": "what's the status",
                          "agent_id": "specter_voss"}
    # stream opened with the same session id
    streams = http.stream_gets()
    assert len(streams) == 1
    assert streams[0][1]["params"] == {"session_id": SESSION}
    assert streams[0][1].get("stream") is True


def test_ask_explicit_agent_id_overrides_default():
    http = FakeHttp()
    c = AgentClient("http://fluid", "specter_voss", http=http)
    reply = c.ask("hi", agent_id="nova_sterling")
    assert reply.agent_id == "nova_sterling"
    assert http.say_posts()[0][1]["json"]["agent_id"] == "nova_sterling"


def test_ask_404_drops_session_remints_once_and_retries():
    http = FakeHttp(say_statuses=[404, 200])
    c = AgentClient("http://fluid", "specter_voss", http=http)
    reply = c.ask("hello")
    # session re-minted: two GET /fluid, two POST /say
    assert len(http.fluid_gets()) == 2
    assert len(http.say_posts()) == 2
    # retry used the re-minted session and the reply still assembles
    assert http.say_posts()[1][1]["json"]["session_id"] == SESSION
    assert reply.text == "Hello Serge. How can I help?"


def test_ask_persistent_404_raises():
    http = FakeHttp(say_statuses=[404, 404])
    c = AgentClient("http://fluid", "specter_voss", http=http)
    with pytest.raises(RuntimeError):
        c.ask("hello")


def test_ask_deadline_returns_partial_and_never_hangs():
    """Fake-clock deadline: only keep-alives after the first sentence.

    The clock advances 5 "seconds" per call; deadline_s=12 means the loop
    must bail after a couple of lines. The consumed-counter proves the
    deadline broke the loop early (it did not just exhaust the iterator).
    """
    consumed = []

    def counting_lines():
        lines = [
            b"event: agent_token",
            b'data: {"agent_id": "specter_voss", "text": "Partial answer.", '
            b'"spoken": true}',
            b"",
        ] + [b": keep-alive", b""] * 50  # no agent_turn_end, ever
        for ln in lines:
            consumed.append(ln)
            yield ln

    http = FakeHttp(stream_lines=counting_lines())
    ticks = iter(range(0, 100000, 5))
    c = AgentClient("http://fluid", "specter_voss", http=http,
                    deadline_s=12.0, _now=lambda: next(ticks))
    reply = c.ask("hi")
    assert reply.text == "Partial answer."
    assert len(consumed) < 10  # deadline stopped the loop, not exhaustion


def test_speak_posts_say_aloud():
    http = FakeHttp()
    c = AgentClient("http://fluid", "specter_voss", http=http)
    c.speak("done", "nova_sterling")
    urls = [p[0] for p in http.posts]
    assert "http://fluid/api/fluid/say_aloud" in urls
    kw = http.posts[-1][1]
    assert kw["json"] == {"text": "done", "agent_id": "nova_sterling"}


def test_speak_empty_text_is_noop():
    http = FakeHttp()
    c = AgentClient("http://fluid", "specter_voss", http=http)
    c.speak("", "nova_sterling")
    assert http.posts == []
