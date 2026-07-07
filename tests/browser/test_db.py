import json


def test_init_and_job_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("PHANTOM_BROWSER_DB", str(tmp_path / "pb.db"))
    from browser import db
    db.init()
    jid = db.create_job("https://example.com", {"max_pages": 5})
    job = db.get_job(jid)
    assert job["status"] == "pending"
    assert json.loads(job["params"])["max_pages"] == 5
    db.set_job_status(jid, "running")
    db.add_page(jid, "https://example.com/", "Example", "# Example")
    db.add_page(jid, "https://example.com/bad", None, None, status="error", error="boom")
    db.set_job_status(jid, "done")
    job = db.get_job(jid)
    assert job["status"] == "done" and job["finished_at"] is not None
    pages = db.job_pages(jid)
    assert len(pages) == 2 and pages[0]["title"] == "Example"


def test_requeue_running(tmp_path, monkeypatch):
    monkeypatch.setenv("PHANTOM_BROWSER_DB", str(tmp_path / "pb.db"))
    from browser import db
    db.init()
    j1 = db.create_job("https://a.com", {})
    db.set_job_status(j1, "running")
    j2 = db.create_job("https://b.com", {})
    assert db.requeue_running() == [j1]
    assert db.get_job(j1)["status"] == "pending"
    assert db.get_job(j2)["status"] == "pending"


def test_cache_roundtrip_and_ttl(tmp_path, monkeypatch):
    monkeypatch.setenv("PHANTOM_BROWSER_DB", str(tmp_path / "pb.db"))
    from browser import db
    db.init()
    assert db.cache_get("https://x.com") is None
    db.cache_put("https://x.com", {"markdown": "hi", "title": "X"})
    assert db.cache_get("https://x.com")["title"] == "X"
    assert db.cache_get("https://x.com", ttl=0) is None
    db.cache_put("https://x.com", {"markdown": "hi2", "title": "X2"})  # upsert
    assert db.cache_get("https://x.com")["title"] == "X2"
