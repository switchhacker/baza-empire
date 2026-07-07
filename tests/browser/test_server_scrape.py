import pytest
from fastapi.testclient import TestClient

FAKE_HTML = "<html><head><title>Fake</title></head><body><article><p>fake body text</p><a href='/next'>next</a></article></body></html>"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PHANTOM_BROWSER_DB", str(tmp_path / "pb.db"))
    from browser import server

    async def fake_start(): ...
    async def fake_stop(): ...
    async def fake_render(url, wait_ms=0, screenshot=False):
        return {"html": FAKE_HTML, "final_url": url, "status": 200, "screenshot_path": None}

    monkeypatch.setattr(server.engine, "start", fake_start)
    monkeypatch.setattr(server.engine, "stop", fake_stop)
    monkeypatch.setattr(server.engine, "render", fake_render)
    with TestClient(server.app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_scrape_returns_markdown(client):
    r = client.post("/scrape", json={"url": "https://fake.test/page"})
    body = r.json()
    assert body["success"] is True
    assert "fake body text" in body["markdown"]
    assert body["title"] == "Fake"
    assert body["links"] == ["https://fake.test/next"]
    assert body["cached"] is False


def test_scrape_cache_hit(client):
    client.post("/scrape", json={"url": "https://fake.test/c"})
    r2 = client.post("/scrape", json={"url": "https://fake.test/c"})
    assert r2.json()["cached"] is True


def test_scrape_error_is_structured(client, monkeypatch):
    from browser import server

    async def boom(url, wait_ms=0, screenshot=False):
        raise TimeoutError("nav timeout")

    monkeypatch.setattr(server.engine, "render", boom)
    r = client.post("/scrape", json={"url": "https://fake.test/err", "no_cache": True})
    body = r.json()
    assert body["success"] is False and "nav timeout" in body["error"]


def test_scrape_non_default_max_chars_bypasses_cache(client):
    """Scrape with default max_chars gets cached; same URL with max_chars=100 bypasses cache."""
    url = "https://fake.test/maxchars"

    # First request with default max_chars=8000 (should cache)
    r1 = client.post("/scrape", json={"url": url})
    assert r1.json()["cached"] is False

    # Second request with same URL and default max_chars (should hit cache)
    r2 = client.post("/scrape", json={"url": url})
    assert r2.json()["cached"] is True

    # Third request with same URL but non-default max_chars=100 (should bypass cache)
    r3 = client.post("/scrape", json={"url": url, "max_chars": 100})
    assert r3.json()["cached"] is False
