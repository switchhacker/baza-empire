import asyncio

import httpx

from browser import crawler as crawler_mod
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


def test_run_crawl_robots_disallowed_skips_scrape(tmp_path, monkeypatch):
    """Finding 3a: a disallowed URL is recorded as an error page and never
    reaches scrape_fn (ignore_robots=False)."""
    monkeypatch.setenv("PHANTOM_BROWSER_DB", str(tmp_path / "pb.db"))
    from browser import db
    db.init()

    calls = []

    async def fake_scrape(url, max_chars=3000, **kw):
        calls.append(url)
        return {"success": True, "title": "t", "markdown": "md", "links": []}

    async def fake_robots(url, ua="PhantomBrowser"):
        return False  # disallow everything

    monkeypatch.setattr(crawler_mod, "robots_allows", fake_robots)

    params = {"url": "https://a.com", "max_pages": 5, "ignore_robots": False}
    jid = db.create_job("https://a.com", params)
    asyncio.run(crawler_mod.run_crawl(jid, fake_scrape, params))

    job = db.get_job(jid)
    assert job["status"] == "done"
    pages = db.job_pages(jid)
    assert len(pages) == 1
    assert pages[0]["status"] == "error"
    assert "robots" in pages[0]["error"]
    assert calls == []  # scrape_fn never invoked for a disallowed page


def test_robots_allows_fetch_failure_fails_open(monkeypatch):
    """Finding 3b: robots.txt fetch failure (network error) fails OPEN,
    matching the previous sync-urllib implementation's failure policy."""
    crawler_mod._robots.clear()

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            raise httpx.ConnectError("boom")

    monkeypatch.setattr(crawler_mod.httpx, "AsyncClient", lambda timeout=5: FakeClient())

    allowed = asyncio.run(crawler_mod.robots_allows("https://unreachable.example/page"))
    assert allowed is True


def test_robots_allows_non_2xx_fails_open(monkeypatch):
    """A non-2xx robots.txt response (e.g. 404/500) also fails open."""
    crawler_mod._robots.clear()

    class FakeResp:
        status_code = 404
        text = ""

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            return FakeResp()

    monkeypatch.setattr(crawler_mod.httpx, "AsyncClient", lambda timeout=5: FakeClient())

    allowed = asyncio.run(crawler_mod.robots_allows("https://fourohfour.example/page"))
    assert allowed is True


def test_run_crawl_setup_error_missing_url(tmp_path, monkeypatch):
    """Finding 2: a setup-phase exception (missing required 'url' param)
    must not escape run_crawl — the job is marked 'error' instead."""
    monkeypatch.setenv("PHANTOM_BROWSER_DB", str(tmp_path / "pb.db"))
    from browser import db
    db.init()

    async def scrape_fn(url, max_chars=3000, **kw):
        return {"success": True, "title": "t", "markdown": "md", "links": []}

    jid = db.create_job("https://a.com", {})
    # no exception should escape asyncio.run — that's the assertion itself
    asyncio.run(run_crawl(jid, scrape_fn, {}))  # missing "url" key

    job = db.get_job(jid)
    assert job["status"] == "error"
    assert job["error"]


def test_run_crawl_resumes_skips_existing_pages(tmp_path, monkeypatch):
    """Finding 4: a requeued job seeds `visited` from prior crawl_pages rows
    so it doesn't re-scrape them or insert duplicate rows, and max_pages
    accounts for the job's lifetime total (pre-existing rows included)."""
    monkeypatch.setenv("PHANTOM_BROWSER_DB", str(tmp_path / "pb.db"))
    from browser import db
    db.init()

    calls = []

    async def fake_scrape(url, max_chars=3000, **kw):
        calls.append(url)
        return {"success": True, "title": "t", "markdown": f"md {url}",
                "links": ["https://a.com/p1"]}

    params = {"url": "https://a.com", "max_pages": 1, "ignore_robots": True}
    jid = db.create_job("https://a.com", params)
    # simulate prior partial run: root already recorded
    db.add_page(jid, "https://a.com/", "root", "md https://a.com/")

    asyncio.run(run_crawl(jid, fake_scrape, params))

    job = db.get_job(jid)
    assert job["status"] == "done"
    pages = db.job_pages(jid)
    assert len(pages) == 1              # no duplicate row for the root page
    assert calls == []                  # root not re-scraped
    # max_pages=1 was already met by the pre-existing row, so the job must
    # not process anything further this run either.
