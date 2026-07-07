import pytest
from fastapi.testclient import TestClient

SITEMAP = """<?xml version="1.0"?><urlset>
<url><loc>https://m.test/page1</loc></url>
<url><loc> https://m.test/page2 </loc></url>
</urlset>"""


def make_client(tmp_path, monkeypatch, sitemap_status=200, sitemap_body=SITEMAP):
    monkeypatch.setenv("PHANTOM_BROWSER_DB", str(tmp_path / "pb.db"))
    from browser import server

    async def fake_start(): ...
    async def fake_stop(): ...
    monkeypatch.setattr(server.engine, "start", fake_start)
    monkeypatch.setattr(server.engine, "stop", fake_stop)

    class FakeResp:
        def __init__(self, status, text):
            self.status_code = status
            self.text = text

    class FakeClient:
        def __init__(self, *a, **kw): ...
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, url, **kw):
            if url.endswith(("sitemap.xml", "sitemap_index.xml")):
                return FakeResp(sitemap_status, sitemap_body)
            return FakeResp(404, "")

    monkeypatch.setattr(server.httpx, "AsyncClient", FakeClient)

    async def fake_scrape(url, max_chars=1000, **kw):
        return {"success": True, "markdown": "", "links": [
            "https://m.test/x", "https://other.test/y", "https://m.test/z"]}

    monkeypatch.setattr(server, "do_scrape", fake_scrape)
    return TestClient(server.app)


def test_map_uses_sitemap(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as c:
        body = c.post("/map", json={"url": "https://m.test/"}).json()
    assert body["success"] is True and body["source"] == "sitemap"
    assert body["urls"] == ["https://m.test/page1", "https://m.test/page2"]


def test_map_falls_back_to_links_same_domain_only(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch, sitemap_status=404, sitemap_body="") as c:
        body = c.post("/map", json={"url": "https://m.test/"}).json()
    assert body["source"] == "links"
    assert body["urls"] == ["https://m.test/x", "https://m.test/z"]


def test_map_respects_limit(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as c:
        body = c.post("/map", json={"url": "https://m.test/", "limit": 1}).json()
    assert body["count"] == 1 and len(body["urls"]) == 1


def test_map_handles_nested_sitemap_with_entities_and_cdata(tmp_path, monkeypatch):
    """Test nested sitemapindex with entity-escaped and CDATA-wrapped URLs."""
    monkeypatch.setenv("PHANTOM_BROWSER_DB", str(tmp_path / "pb.db"))
    from browser import server

    async def fake_start(): ...
    async def fake_stop(): ...
    monkeypatch.setattr(server.engine, "start", fake_start)
    monkeypatch.setattr(server.engine, "stop", fake_stop)

    class FakeResp:
        def __init__(self, status, text):
            self.status_code = status
            self.text = text

    # Parent sitemapindex with entity-escaped child URL
    parent_sitemapindex = """<?xml version="1.0"?><sitemapindex>
<sitemap><loc>https://x.test/sitemap2.xml</loc></sitemap>
</sitemapindex>"""

    # Child urlset with entity-escaped URL and CDATA-wrapped URL
    child_urlset = """<?xml version="1.0"?><urlset>
<url><loc>https://x.test/page?a=1&amp;b=2</loc></url>
<url><loc><![CDATA[https://x.test/cdata-page]]></loc></url>
</urlset>"""

    class FakeClient:
        def __init__(self, *a, **kw): ...
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, url, **kw):
            if url.endswith(("sitemap.xml", "sitemap_index.xml")):
                return FakeResp(200, parent_sitemapindex)
            elif url == "https://x.test/sitemap2.xml":
                return FakeResp(200, child_urlset)
            return FakeResp(404, "")

    monkeypatch.setattr(server.httpx, "AsyncClient", FakeClient)

    async def fake_scrape(url, max_chars=1000, **kw):
        return {"success": True, "markdown": "", "links": []}

    monkeypatch.setattr(server, "do_scrape", fake_scrape)

    client = TestClient(server.app)
    body = client.post("/map", json={"url": "https://x.test/"}).json()

    assert body["success"] is True
    assert body["source"] == "sitemap"
    # URLs should be unescaped and CDATA markers removed
    assert "https://x.test/page?a=1&b=2" in body["urls"]
    assert "https://x.test/cdata-page" in body["urls"]
    assert len(body["urls"]) == 2
