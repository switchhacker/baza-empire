import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PHANTOM_BROWSER_DB", str(tmp_path / "pb.db"))
    from browser import server

    async def fake_start(): ...
    async def fake_stop(): ...
    monkeypatch.setattr(server.engine, "start", fake_start)
    monkeypatch.setattr(server.engine, "stop", fake_stop)

    async def fake_scrape(url, max_chars=8000, **kw):
        return {"success": True, "markdown": f"# Page {url}\nvendor: HD total: 9.5"}

    async def fake_extract(content, schema, prompt=None, model=None):
        assert "vendor: HD" in content
        return {"success": True, "data": {"vendor": "HD"}, "model": "fake"}

    monkeypatch.setattr(server, "do_scrape", fake_scrape)
    monkeypatch.setattr(server.extractor, "extract", fake_extract)
    with TestClient(server.app) as c:
        yield c


def test_extract_from_url(client):
    r = client.post("/extract", json={
        "url": "https://e.test/p",
        "schema": {"type": "object", "required": ["vendor"],
                   "properties": {"vendor": {"type": "string"}}},
    })
    body = r.json()
    assert body["success"] is True
    assert body["data"] == {"vendor": "HD"}
    assert body["sources"] == ["https://e.test/p"]


def test_extract_requires_input(client):
    r = client.post("/extract", json={"schema": {"type": "object"}})
    assert r.json()["success"] is False


def test_extract_sources_respect_content_budget(client, monkeypatch):
    # 4 URLs each contribute ~8000 chars of markdown against a 24000-char
    # content budget. Only the URLs whose content actually made it into the
    # window handed to extract() should be listed in `sources`.
    from browser import server

    async def fake_scrape_big(url, max_chars=8000, **kw):
        return {"success": True, "markdown": "x" * 8000}

    captured = {}

    async def fake_extract_capture(content, schema, prompt=None, model=None):
        captured["content"] = content
        return {"success": True, "data": {"vendor": "HD"}, "model": "fake"}

    monkeypatch.setattr(server, "do_scrape", fake_scrape_big)
    monkeypatch.setattr(server.extractor, "extract", fake_extract_capture)

    urls = [f"https://e.test/p{i}" for i in range(4)]
    r = client.post("/extract", json={
        "urls": urls,
        "schema": {"type": "object", "required": ["vendor"],
                   "properties": {"vendor": {"type": "string"}}},
    })
    body = r.json()
    assert body["success"] is True
    assert len(body["sources"]) == 3
    assert body["sources"] == urls[:3]
    assert len(captured["content"]) <= 24000
