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
