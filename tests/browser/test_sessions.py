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
    async def evaluate(self, *a, **kw):
        return None


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
