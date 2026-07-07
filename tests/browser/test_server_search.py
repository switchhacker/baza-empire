import pytest
from fastapi.testclient import TestClient

SEARX_JSON = {
    "query": "widgets",
    "results": [
        {"title": "Widget World", "url": "https://w.test/a", "content": "all about widgets"},
        {"title": "Widget FAQ", "url": "https://w.test/b", "content": "faq"},
        {"title": "Extra", "url": "https://w.test/c", "content": "x"},
    ],
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PHANTOM_BROWSER_DB", str(tmp_path / "pb.db"))
    from browser import server

    async def fake_start(): ...
    async def fake_stop(): ...
    monkeypatch.setattr(server.engine, "start", fake_start)
    monkeypatch.setattr(server.engine, "stop", fake_stop)

    class FakeResp:
        status_code = 200
        def json(self):
            return SEARX_JSON
        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *a, **kw): ...
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, url, **kw):
            assert "/search" in url
            return FakeResp()

    monkeypatch.setattr(server.httpx, "AsyncClient", FakeClient)

    async def fake_scrape(url, max_chars=3000, **kw):
        return {"success": True, "markdown": f"content-of {url}"}

    monkeypatch.setattr(server, "do_scrape", fake_scrape)
    with TestClient(server.app) as c:
        yield c


def test_search_maps_results(client):
    r = client.post("/search", json={"query": "widgets", "n": 2})
    body = r.json()
    assert body["success"] is True and body["source"] == "searxng"
    assert len(body["results"]) == 2
    assert body["results"][0] == {
        "title": "Widget World", "url": "https://w.test/a", "snippet": "all about widgets",
    }


def test_search_fetch_content(client):
    r = client.post("/search", json={"query": "widgets", "n": 1, "fetch_content": True})
    body = r.json()
    assert body["results"][0]["content"] == "content-of https://w.test/a"


def test_search_searxng_down_structured_error(client, monkeypatch):
    """Test that SearXNG connection failure returns proper error response."""
    import httpx

    class FailingFakeClient:
        def __init__(self, *a, **kw): ...
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, url, **kw):
            raise httpx.ConnectError("connection refused")

    from browser import server
    monkeypatch.setattr(server.httpx, "AsyncClient", FailingFakeClient)

    r = client.post("/search", json={"query": "test", "n": 5})
    body = r.json()
    assert body["success"] is False
    assert body["query"] == "test"
    assert "error" in body
    assert len(body["error"]) > 0


def test_search_fetch_content_isolates_failures(client, monkeypatch):
    """Test that fetch_content failures are isolated per result."""
    from browser import server

    async def selective_scrape(url, max_chars=3000, **kw):
        if "w.test/b" in url:
            # Simulate a failure for the second URL
            raise RuntimeError("scrape failed")
        return {"success": True, "markdown": f"content-of {url}"}

    monkeypatch.setattr(server, "do_scrape", selective_scrape)

    r = client.post("/search", json={"query": "widgets", "n": 2, "fetch_content": True})
    body = r.json()
    assert body["success"] is True
    assert len(body["results"]) == 2

    # First result should have content
    assert body["results"][0]["content"] == "content-of https://w.test/a"

    # Second result should have failure placeholder
    assert "(fetch failed" in body["results"][1]["content"]
