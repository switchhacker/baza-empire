import time

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

    async def fake_scrape(url, max_chars=3000, **kw):
        return {"success": True, "title": "T", "markdown": f"md {url}", "links": []}

    monkeypatch.setattr(server, "do_scrape", fake_scrape)
    with TestClient(server.app) as c:
        yield c


def test_crawl_job_lifecycle(client):
    r = client.post("/crawl", json={"url": "https://c.test/", "max_pages": 2,
                                    "ignore_robots": True})
    jid = r.json()["job_id"]
    assert r.json()["success"] is True
    for _ in range(50):  # poll until background task finishes
        body = client.get(f"/crawl/{jid}").json()
        if body["job"]["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert body["job"]["status"] == "done"
    assert body["pages"][0]["markdown"] == "md https://c.test/"


def test_crawl_get_without_content(client):
    r = client.post("/crawl", json={"url": "https://c.test/", "max_pages": 1,
                                    "ignore_robots": True})
    jid = r.json()["job_id"]
    for _ in range(50):
        body = client.get(f"/crawl/{jid}?include_content=false").json()
        if body["job"]["status"] == "done":
            break
        time.sleep(0.1)
    assert "markdown" not in body["pages"][0]


def test_crawl_unknown_job(client):
    assert client.get("/crawl/99999").json()["success"] is False


def test_crawl_background_task_is_tracked(client):
    """Finding 1: the background crawl task must be retained (not just
    fire-and-forget create_task) so it can't be garbage-collected mid-run,
    and released once it completes."""
    from browser import server

    r = client.post("/crawl", json={"url": "https://c.test/", "max_pages": 1,
                                    "ignore_robots": True})
    jid = r.json()["job_id"]
    assert len(server._crawl_tasks) >= 1  # launch retains a strong reference

    for _ in range(50):  # poll until background task finishes
        body = client.get(f"/crawl/{jid}").json()
        if body["job"]["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert body["job"]["status"] == "done"

    time.sleep(0.05)  # let the done-callback fire
    assert all(t.done() for t in server._crawl_tasks)
