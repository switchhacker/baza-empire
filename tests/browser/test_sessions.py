import asyncio

import pytest

from browser import db
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


@pytest.mark.integration
def test_click_without_index_structured_error(tmp_path):
    url = make_pages(tmp_path)

    async def go(mgr):
        sid = await mgr.create()
        await mgr.act(sid, "goto", url=url)
        # no index kwarg at all — must not raise a raw TypeError
        return await mgr.act(sid, "click")

    out = asyncio.run(with_mgr(go))
    assert out["success"] is False
    assert "index" in out["error"]


class _FakeChromePage:
    def __init__(self):
        self.url = "https://example.com"

    async def evaluate(self, *a, **kw):
        return None

    async def goto(self, url, **kw):
        self.url = url

    async def go_back(self, **kw):
        pass


class _FakeContext:
    def __init__(self):
        self.pages = []

    async def new_page(self):
        # yield control so concurrent create() calls can interleave here if
        # they aren't properly serialized by the manager's create lock
        await asyncio.sleep(0.01)
        return _FakeChromePage()

    async def close(self):
        pass


class _FakeEngine:
    async def new_context(self, profile=None):
        await asyncio.sleep(0.01)
        return _FakeContext()


def test_max_sessions_concurrent_creates():
    async def go():
        mgr = SessionManager(_FakeEngine(), max_sessions=2)
        results = await asyncio.gather(
            *(mgr.create() for _ in range(4)), return_exceptions=True
        )
        return mgr, results

    mgr, results = asyncio.run(go())
    assert len(mgr._sessions) <= 2
    errors = [r for r in results if isinstance(r, RuntimeError)]
    assert len(errors) >= 2


# ── Finding 2a: pending-approval freeze (SessionManager-level) ─────────────
#
# pending_block()/act() are DB-authoritative (round 2 review, finding 1):
# they look up the real approval row via db.get_approval(), not just the
# in-memory marker. So every test below that exercises the freeze needs a
# genuine 'pending' row backing the approval id — _pending_approval() makes
# one in an isolated tmp DB (same PHANTOM_BROWSER_DB-env pattern as
# test_server_gate.py's client fixture).

def _pending_approval(monkeypatch, tmp_path, session_id: str) -> int:
    monkeypatch.setenv("PHANTOM_BROWSER_DB", str(tmp_path / "pb.db"))
    db.init()
    return db.create_approval(session_id, {"op": "test"}, "test", "tok")


def test_pending_approval_blocks_mutating_ops(tmp_path, monkeypatch):
    """Once a session is marked with a pending approval, act() must refuse
    goto/click/type/press/scroll without touching the page at all."""
    async def go():
        mgr = SessionManager(_FakeEngine())
        sid = await mgr.create()
        aid = _pending_approval(monkeypatch, tmp_path, sid)
        mgr.mark_pending_approval(sid, aid)
        return aid, await mgr.act(sid, "goto", url="https://example.com")

    aid, result = asyncio.run(go())
    assert result == {"success": False,
                       "error": "approval pending; resolve it before acting",
                       "approval_id": aid}


def test_pending_block_helper_reports_and_clears(tmp_path, monkeypatch):
    async def go():
        mgr = SessionManager(_FakeEngine())
        sid = await mgr.create()
        before = mgr.pending_block(sid)
        aid = _pending_approval(monkeypatch, tmp_path, sid)
        mgr.mark_pending_approval(sid, aid)
        during = mgr.pending_block(sid)
        mgr.clear_pending_approval(sid)
        after = mgr.pending_block(sid)
        return aid, before, during, after

    aid, before, during, after = asyncio.run(go())
    assert before is None
    assert during == {"success": False,
                       "error": "approval pending; resolve it before acting",
                       "approval_id": aid}
    assert after is None


def test_clear_pending_approval_unfreezes_session(tmp_path, monkeypatch):
    async def go():
        mgr = SessionManager(_FakeEngine())
        sid = await mgr.create()
        aid = _pending_approval(monkeypatch, tmp_path, sid)
        mgr.mark_pending_approval(sid, aid)
        blocked = await mgr.act(sid, "goto", url="https://example.com")
        mgr.clear_pending_approval(sid)
        return blocked, mgr.get(sid).pending_approval_id

    blocked, pid = asyncio.run(go())
    assert blocked["success"] is False
    assert pid is None


# ── Finding 1 (round 2 review): reaper-expiry self-heal ────────────────────

def test_pending_block_self_heals_after_reaper_expiry(tmp_path, monkeypatch):
    """The reaper's db.expire_stale(300) sweep (silence = denied) can flip a
    pending approval straight to 'expired' with NO /approvals/{id}/decide
    hit ever landing on this session — the reaper only knows about approval
    rows, not sessions, so it can't clear the session's own marker. Once
    the approval is no longer 'pending' in the DB, pending_block() (and
    therefore act()) must notice on its own and self-heal the freeze rather
    than bricking every future mutating op on the session forever."""
    async def go():
        mgr = SessionManager(_FakeEngine())
        sid = await mgr.create()
        aid = _pending_approval(monkeypatch, tmp_path, sid)
        mgr.mark_pending_approval(sid, aid)

        blocked = await mgr.act(sid, "goto", url="https://example.com")
        db.expire_stale(0)  # reaper sweep; never touches session state directly
        allowed = await mgr.act(sid, "goto", url="https://example.com")
        return aid, blocked, allowed, mgr.get(sid).pending_approval_id

    aid, blocked, allowed, pid_after = asyncio.run(go())
    assert blocked == {"success": False,
                        "error": "approval pending; resolve it before acting",
                        "approval_id": aid}
    assert allowed["success"] is True                   # no permanent brick
    assert pid_after is None                             # marker self-cleared


# ── Finding 2 (round 2 review): back is a mutating op too ──────────────────

def test_back_is_frozen_by_pending_approval(tmp_path, monkeypatch):
    """MUTATING_OPS must include 'back' so a pending approval freezes it
    exactly like goto/click/type/press/scroll — otherwise 'back' is a
    bypass an agent can use between a gated request and Serge's decision."""
    async def go():
        mgr = SessionManager(_FakeEngine())
        sid = await mgr.create()
        aid = _pending_approval(monkeypatch, tmp_path, sid)
        mgr.mark_pending_approval(sid, aid)
        return aid, await mgr.act(sid, "back")

    aid, result = asyncio.run(go())
    assert result == {"success": False,
                       "error": "approval pending; resolve it before acting",
                       "approval_id": aid}


# ── Finding 3 (round 2 review): ELEMENT_INFO_JS surfaces href ──────────────

@pytest.mark.integration
def test_element_info_returns_href_for_anchor(tmp_path):
    """server.py's click-gating decision needs the anchor's href to catch a
    neutral-text link that navigates to a mutation URL (Playwright's
    click-navigation never routes through goto/is_gated_goto on its own) —
    ELEMENT_INFO_JS must surface it."""
    url = make_pages(tmp_path)

    async def go(mgr):
        sid = await mgr.create()
        await mgr.act(sid, "goto", url=url)
        read1 = await mgr.read(sid)
        link = next(e for e in read1["elements"] if "Next page" in e["text"])
        return await mgr.element_info(sid, link["idx"])

    info = asyncio.run(with_mgr(go))
    assert info["href"].endswith("page2.html")


@pytest.mark.integration
def test_element_info_href_empty_for_non_anchor(tmp_path):
    url = make_pages(tmp_path)

    async def go(mgr):
        sid = await mgr.create()
        await mgr.act(sid, "goto", url=url)
        read1 = await mgr.read(sid)
        btn = next(e for e in read1["elements"] if "Send it" in e["text"])
        return await mgr.element_info(sid, btn["idx"])

    info = asyncio.run(with_mgr(go))
    assert info["href"] == ""


def test_clear_pending_approval_is_aid_bound(tmp_path, monkeypatch):
    """Deciding approval aid1 must NOT unfreeze a session whose live marker
    belongs to a still-pending aid2 (concurrent-gated-request race)."""
    monkeypatch.setenv("PHANTOM_BROWSER_DB", str(tmp_path / "pb.db"))
    from browser.sessions import SessionManager

    class _S:
        def __init__(self): self.pending_approval_id = None
        def touch(self): pass
    mgr = SessionManager.__new__(SessionManager)
    mgr._sessions = {"s1": _S()}
    mgr.mark_pending_approval("s1", 2)          # live marker = aid2
    mgr.clear_pending_approval("s1", 1)         # decide aid1 → must NOT clear
    assert mgr._sessions["s1"].pending_approval_id == 2
    mgr.clear_pending_approval("s1", 2)         # decide aid2 → clears
    assert mgr._sessions["s1"].pending_approval_id is None
    mgr.mark_pending_approval("s1", 5)
    mgr.clear_pending_approval("s1")            # force-clear (self-heal path)
    assert mgr._sessions["s1"].pending_approval_id is None
