import importlib, os, sys
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "dashboard"))


@pytest.fixture
def es():
    sys.modules.pop("email_studio", None)
    return importlib.import_module("email_studio")


def test_sanitize_strips_scripts_and_handlers(es):
    dirty = ('<div onclick="steal()">hi</div>'
             '<script>alert(1)</script>'
             '<img src="x" onerror="alert(2)">'
             '<a href="javascript:evil()">c</a>'
             '<iframe src="https://evil"></iframe>'
             '<form action="https://evil"><input></form>')
    clean = es._sanitize_email_html(dirty)
    low = clean.lower()
    assert "<script" not in low and "alert(1)" not in low
    assert "onclick" not in low and "onerror" not in low
    assert "javascript:" not in low
    assert "<iframe" not in low and "<form" not in low
    assert "hi" in clean  # content survives


def test_sanitize_keeps_formatting(es):
    html = '<table><tr><td style="color:red">cell</td></tr></table><style>.x{color:blue}</style>'
    clean = es._sanitize_email_html(html)
    assert "<table>" in clean and 'style="color:red"' in clean and "<style>" in clean


def test_sanitize_rewrites_cid(es):
    html = '<img src="cid:logo123" alt="l">'
    clean = es._sanitize_email_html(html, {"logo123": "/api/email2/attachment/M1/A1?inline=1"})
    assert 'src="/api/email2/attachment/M1/A1?inline=1"' in clean
    assert "cid:" not in clean


def test_sanitize_strips_slash_separated_handlers(es):
    """FINDING 1: HTML5 allows '/' as an attribute separator, not just
    whitespace, so <svg/onload=...> and <img/onerror=... src=x> must also
    have their event handlers stripped."""
    dirty = '<svg/onload=alert(1)>' '<img/onerror=alert(1) src=x>'
    clean = es._sanitize_email_html(dirty)
    low = clean.lower()
    assert "onload" not in low
    assert "onerror" not in low
    assert "alert(1)" not in low


def test_sanitize_strips_obfuscated_js_schemes(es):
    """FINDING 2: the scheme filter must survive HTML-entity encoding of the
    colon and embedded whitespace/control chars inside the scheme, since
    browsers unescape entities and strip \\x00-\\x20 before parsing the URL
    scheme (WHATWG URL spec)."""
    entity_colon = '<a href="javascript&colon;alert(1)">c</a>'
    tab_split = '<a href="java\tscript:alert(1)">c</a>'
    newline_split = '<a href="java\nscript:alert(1)">c</a>'

    for dirty in (entity_colon, tab_split, newline_split):
        clean = es._sanitize_email_html(dirty)
        low = clean.lower()
        assert "javascript:" not in low, f"javascript: scheme survived: {clean!r}"
        assert "alert(1)" not in low, f"payload survived: {clean!r}"


def test_sanitize_preserves_legit_urls(es):
    """The URL-attribute callback must not mangle non-malicious schemes —
    including cid: (rewritten separately, later, by cid_map) and ordinary
    https: links."""
    html = '<a href="https://x.com/a?b=c">link</a><img src="cid:abc">'
    clean = es._sanitize_email_html(html)
    assert 'href="https://x.com/a?b=c"' in clean
    assert 'src="cid:abc"' in clean


def test_sanitize_preserves_urls_with_on_word_params(es):
    """FINDING A (fix round 2): the event-handler strip regex is
    quote-context-blind. Benign markup where a URL/attribute value contains
    "on<word>=" as a substring (tracking params like on2=, online=, onsale=,
    onboarding=, preceded by "/" or whitespace) must survive intact — no
    truncated attribute values, no eaten closing tags."""
    html = '<a href="https://x.com/p/on2=abc?online=1">hi</a>'
    clean = es._sanitize_email_html(html)
    assert 'href="https://x.com/p/on2=abc?online=1"' in clean
    assert "</a>" in clean

    html2 = '<p title="settings/onload=danger">x</p>'
    clean2 = es._sanitize_email_html(html2)
    assert 'title="settings/onload=danger"' in clean2
    assert "</p>" in clean2


def test_sanitize_still_strips_slash_handlers_after_finding_a_fix(es):
    """Regression guard: the Finding A quote-awareness fix must not weaken
    the slash-separated-handler coverage from fix round 1."""
    dirty = '<svg/onload=alert(1)>' '<img/onerror=alert(1) src=x>'
    clean = es._sanitize_email_html(dirty)
    low = clean.lower()
    assert "onload" not in low
    assert "onerror" not in low
    assert "alert(1)" not in low


def test_sanitize_css_url_js_scheme(es):
    """FINDING B (fix round 2): the CSS url() javascript:/vbscript: filter
    must survive the same entity-encoding / control-char-splitting bypasses
    as the URL-attribute filter (fix round 1, Finding 2), not just a literal
    "javascript:" substring match. Legitimate url() values must survive."""
    entity_colon = 'style="background:url(javascript&colon;alert(1))"'
    tab_split = 'style="background:url(java\tscript:alert(1))"'
    for dirty in (entity_colon, tab_split):
        clean = es._sanitize_email_html(f"<div {dirty}>x</div>")
        low = clean.lower()
        assert "javascript" not in low, f"javascript scheme survived: {clean!r}"
        assert "alert(1)" not in low, f"payload survived: {clean!r}"

    safe = '<div style="background:url(https://x.com/a.png)">x</div>'
    clean_safe = es._sanitize_email_html(safe)
    assert "url(https://x.com/a.png)" in clean_safe

    cid = '<div style="background:url(cid:logo)">x</div>'
    clean_cid = es._sanitize_email_html(cid)
    assert "url(cid:logo)" in clean_cid


def _client(es, monkeypatch, payload):
    class FakeSvc:
        def users(self): return self
        def messages(self): return self
        def get(self, userId, id, format): return self
        def execute(self): return {"id": "M1", "payload": payload}
    monkeypatch.setattr(es, "_req_account_id", lambda: None)
    monkeypatch.setattr(es, "_gmail", lambda a: FakeSvc())
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(es.email_bp)
    app.config["TESTING"] = True
    return app.test_client()


def test_html_endpoint_returns_sanitized_doc(es, monkeypatch):
    import base64
    raw = base64.urlsafe_b64encode(b"<p>Hello <b>world</b></p><script>x()</script>").decode()
    payload = {"mimeType": "text/html", "body": {"data": raw}}
    c = _client(es, monkeypatch, payload)
    r = c.get("/api/email2/message/M1/html")
    assert r.status_code == 200
    assert r.mimetype == "text/html"
    assert "script-src 'none'" in r.headers.get("Content-Security-Policy", "")
    body = r.get_data(as_text=True)
    assert "<b>world</b>" in body and "<script" not in body.lower()


def test_html_endpoint_404_when_plain_only(es, monkeypatch):
    import base64
    raw = base64.urlsafe_b64encode(b"just text").decode()
    payload = {"mimeType": "text/plain", "body": {"data": raw}}
    c = _client(es, monkeypatch, payload)
    r = c.get("/api/email2/message/M1/html")
    assert r.status_code == 404
