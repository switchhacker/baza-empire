import json
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PHANTOM_BROWSER_DB", str(tmp_path / "pb.db"))
    from browser import server, gate

    async def fake_start(): ...
    async def fake_stop(): ...
    monkeypatch.setattr(server.engine, "start", fake_start)
    monkeypatch.setattr(server.engine, "stop", fake_stop)
    monkeypatch.setattr(gate, "_send_telegram", lambda msg: True)

    # fake session layer: one profile session 'psess', one anon 'asess'
    class FakeSessions:
        def __init__(self):
            self.executed = []
        def get(self, sid):
            class S:  # minimal stand-in
                profile = "gmail" if sid == "psess" else None
            if sid not in ("psess", "asess"):
                raise KeyError(sid)
            return S()
        async def element_info(self, sid, index):
            return {"tag": "button", "type": "submit", "text": "Send it",
                    "in_form": True, "form_method": "post"}
        async def active_element(self, sid):
            return None
        async def act(self, sid, op, **kw):
            self.executed.append((sid, op, kw))
            return {"success": True, "url": "https://x.test/done"}
        async def read(self, sid, max_chars=6000):
            return {"success": True, "url": "u", "title": "t", "markdown": "m",
                    "elements": []}
        async def close_all(self):
            # real SessionManager.close_all() is invoked unconditionally by
            # server.py's lifespan shutdown; the fake needs the same shape
            # so TestClient's context-manager exit doesn't error in teardown.
            pass

    fake = FakeSessions()
    monkeypatch.setattr(server, "sessions", fake)
    with TestClient(server.app) as c:
        c.fake_sessions = fake
        yield c


def test_gated_click_returns_pending(client):
    r = client.post("/session/psess/click", json={"index": 3})
    body = r.json()
    assert body["status"] == "pending_approval"
    assert client.fake_sessions.executed == []          # nothing ran


def test_anon_click_not_gated(client):
    r = client.post("/session/asess/click", json={"index": 3})
    assert r.json()["success"] is True
    assert ("asess", "click", {"index": 3}) in client.fake_sessions.executed


def test_approve_executes_queued_action(client):
    aid = client.post("/session/psess/click", json={"index": 3}).json()["approval_id"]
    from browser import db
    tok = db.get_approval(aid)["token"]
    r = client.get(f"/approvals/{aid}/decide", params={"tok": tok, "d": "approve"})
    assert r.status_code == 200
    assert db.get_approval(aid)["status"] == "executed"
    assert ("psess", "click", {"index": 3}) in client.fake_sessions.executed


def test_deny_blocks_action(client):
    aid = client.post("/session/psess/click", json={"index": 3}).json()["approval_id"]
    from browser import db
    tok = db.get_approval(aid)["token"]
    client.get(f"/approvals/{aid}/decide", params={"tok": tok, "d": "deny"})
    assert db.get_approval(aid)["status"] == "denied"
    assert client.fake_sessions.executed == []


def test_bad_token_rejected(client):
    aid = client.post("/session/psess/click", json={"index": 3}).json()["approval_id"]
    r = client.get(f"/approvals/{aid}/decide", params={"tok": "wrong", "d": "approve"})
    assert r.status_code == 403


def test_approval_status_endpoint(client):
    aid = client.post("/session/psess/click", json={"index": 3}).json()["approval_id"]
    body = client.get(f"/approvals/{aid}").json()
    assert body["status"] == "pending"


def test_stale_approval_blocked_at_decide_time(client):
    """A pending approval past the 300s deadline must be denied at decision
    time, not left to the lagging 60s reaper sweep — a late approve must not
    execute."""
    aid = client.post("/session/psess/click", json={"index": 3}).json()["approval_id"]
    from browser import db
    tok = db.get_approval(aid)["token"]

    # Backdate created_at directly so the row is still 'pending' (the reaper
    # hasn't swept it) but is past the deadline the decide route must enforce.
    conn = db.connect()
    conn.execute("UPDATE approvals SET created_at=? WHERE id=?",
                 (time.time() - 301, aid))
    conn.commit()
    conn.close()

    r = client.get(f"/approvals/{aid}/decide", params={"tok": tok, "d": "approve"})
    assert r.status_code == 200
    assert "expired" in r.text.lower()
    assert db.get_approval(aid)["status"] == "expired"
    assert client.fake_sessions.executed == []          # never ran


def test_approve_replay_does_not_reexecute(client):
    """Hitting the same approve URL twice must only execute the action once —
    the status guard has to block the replay."""
    aid = client.post("/session/psess/click", json={"index": 3}).json()["approval_id"]
    from browser import db
    tok = db.get_approval(aid)["token"]

    r1 = client.get(f"/approvals/{aid}/decide", params={"tok": tok, "d": "approve"})
    assert r1.status_code == 200
    assert db.get_approval(aid)["status"] == "executed"
    assert len(client.fake_sessions.executed) == 1

    r2 = client.get(f"/approvals/{aid}/decide", params={"tok": tok, "d": "approve"})
    assert r2.status_code == 200
    assert "already" in r2.text.lower()
    assert len(client.fake_sessions.executed) == 1      # not re-executed
    assert db.get_approval(aid)["status"] == "executed"


def test_gated_press_returns_pending_and_creates_approval(client, monkeypatch):
    """press-route gate wiring: a gated active element must pause for
    approval instead of running the keypress immediately."""
    async def fake_active(sid):
        return {"tag": "input", "type": "text", "text": "Submit order",
                "in_form": True, "form_method": "post"}
    monkeypatch.setattr(client.fake_sessions, "active_element", fake_active)

    r = client.post("/session/psess/press", json={"key": "Enter"})
    body = r.json()
    assert body["status"] == "pending_approval"

    from browser import db
    approval = db.get_approval(body["approval_id"])
    assert approval is not None
    assert approval["status"] == "pending"
    assert client.fake_sessions.executed == []          # sessions.act NOT called yet
