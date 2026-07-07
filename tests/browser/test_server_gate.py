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
            self._pending = {}          # sid -> approval_id
            self._element_info = {"tag": "button", "type": "submit", "text": "Send it",
                                   "in_form": True, "form_method": "post"}
        def get(self, sid):
            if sid not in ("psess", "asess"):
                raise KeyError(sid)
            outer = self

            class S:  # minimal stand-in
                profile = "gmail" if sid == "psess" else None
                pending_approval_id = outer._pending.get(sid)
            return S()
        def pending_block(self, sid):
            if sid not in ("psess", "asess"):
                raise KeyError(sid)
            pid = self._pending.get(sid)
            if pid is None:
                return None
            return {"success": False, "error": "approval pending; resolve it before acting",
                    "approval_id": pid}
        def mark_pending_approval(self, sid, approval_id):
            self._pending[sid] = approval_id
        def clear_pending_approval(self, sid):
            self._pending.pop(sid, None)
        async def element_info(self, sid, index):
            return dict(self._element_info)
        async def active_element(self, sid):
            return None
        async def act(self, sid, op, **kw):
            if op in ("goto", "click", "type", "press", "scroll", "back") and self._pending.get(sid) is not None:
                return {"success": False, "error": "approval pending; resolve it before acting",
                        "approval_id": self._pending[sid]}
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


# ── Finding 1: goto is a write channel too ──────────────────────────────────

def test_profile_goto_with_query_gated(client):
    """A profile-session goto to a GET-triggered mutation URL must gate,
    not navigate the authenticated context straight through."""
    r = client.post("/session/psess/goto", json={"url": "https://x.test/cart?action=delete"})
    body = r.json()
    assert body["status"] == "pending_approval"
    assert client.fake_sessions.executed == []          # nothing ran

    from browser import db
    approval = db.get_approval(body["approval_id"])
    assert approval is not None and approval["status"] == "pending"


def test_profile_goto_verb_url_gated(client):
    """GATED_RX-matching path (no query string) must also gate."""
    r = client.post("/session/psess/goto", json={"url": "https://x.test/cart/checkout/confirm"})
    assert r.json()["status"] == "pending_approval"
    assert client.fake_sessions.executed == []


def test_profile_goto_approve_executes(client):
    aid = client.post("/session/psess/goto",
                       json={"url": "https://x.test/cart?action=delete"}).json()["approval_id"]
    from browser import db
    tok = db.get_approval(aid)["token"]
    r = client.get(f"/approvals/{aid}/decide", params={"tok": tok, "d": "approve"})
    assert r.status_code == 200
    assert db.get_approval(aid)["status"] == "executed"
    assert ("psess", "goto", {"url": "https://x.test/cart?action=delete"}) \
        in client.fake_sessions.executed


def test_anon_goto_with_query_not_gated(client):
    """Anonymous sessions stay ungated regardless of the URL shape."""
    r = client.post("/session/asess/goto", json={"url": "https://x.test/cart?action=delete"})
    assert r.json()["success"] is True
    assert ("asess", "goto", {"url": "https://x.test/cart?action=delete"}) \
        in client.fake_sessions.executed


def test_profile_plain_goto_not_gated(client):
    """A plain navigation (no query string, no mutation verb) must not gate
    — otherwise normal browsing in a profile session breaks."""
    r = client.post("/session/psess/goto", json={"url": "https://example.com"})
    assert r.json()["success"] is True
    assert ("psess", "goto", {"url": "https://example.com"}) in client.fake_sessions.executed


# ── Finding 2a: pending-approval freeze ─────────────────────────────────────

def test_pending_approval_freezes_further_acts(client):
    """While a gated click's approval is pending, any further mutating act on
    that session (goto/click/type) must refuse instead of executing."""
    aid = client.post("/session/psess/click", json={"index": 3}).json()["approval_id"]

    r_goto = client.post("/session/psess/goto", json={"url": "https://example.com"})
    assert r_goto.json()["success"] is False
    assert "approval pending" in r_goto.json()["error"]
    assert r_goto.json()["approval_id"] == aid

    r_click = client.post("/session/psess/click", json={"index": 5})
    assert r_click.json()["success"] is False
    assert "approval pending" in r_click.json()["error"]

    r_type = client.post("/session/psess/type", json={"index": 1, "text": "hi"})
    assert r_type.json()["success"] is False
    assert "approval pending" in r_type.json()["error"]

    assert client.fake_sessions.executed == []          # nothing ran

    from browser import db
    tok = db.get_approval(aid)["token"]
    client.get(f"/approvals/{aid}/decide", params={"tok": tok, "d": "deny"})

    # session usable again after the approval is decided
    r = client.post("/session/psess/goto", json={"url": "https://example.com"})
    assert r.json()["success"] is True


# ── Finding 2b: element-identity binding ────────────────────────────────────

def test_element_drift_refuses_execution(client, monkeypatch):
    """The approval was described against index N's element (text "Send it").
    If that index now resolves to a different element by the time Serge
    approves, the replay must refuse rather than click the new element."""
    aid = client.post("/session/psess/click", json={"index": 3}).json()["approval_id"]

    async def drifted_element_info(sid, index):
        return {"tag": "button", "type": "submit", "text": "Pay",
                "in_form": True, "form_method": "post"}
    monkeypatch.setattr(client.fake_sessions, "element_info", drifted_element_info)

    from browser import db
    tok = db.get_approval(aid)["token"]
    r = client.get(f"/approvals/{aid}/decide", params={"tok": tok, "d": "approve"})
    assert "element changed" in r.text.lower()
    assert client.fake_sessions.executed == []          # NOT executed
    assert db.get_approval(aid)["status"] == "expired"

    # freeze must also be cleared so the session isn't stuck
    r2 = client.post("/session/psess/goto", json={"url": "https://example.com"})
    assert r2.json()["success"] is True


# ── Finding 1 (round 2 review): reaper-expiry must not permanently brick ───

def test_decide_after_reaper_expiry_clears_freeze(client):
    """If the reaper's db.expire_stale() sweep flips a pending approval to
    'expired' before anyone ever hits the decide link (no browser click at
    all — the reaper doesn't know about sessions), a *later* hit on
    /approvals/{id}/decide must still report the terminal status without
    executing AND must clear the session's freeze marker as a
    belt-and-suspenders on top of pending_block()'s own DB-authoritative
    self-heal."""
    aid = client.post("/session/psess/click", json={"index": 3}).json()["approval_id"]

    from browser import db
    db.expire_stale(0)  # simulate the reaper's silence=denied sweep firing first
    assert db.get_approval(aid)["status"] == "expired"

    tok = db.get_approval(aid)["token"]
    r = client.get(f"/approvals/{aid}/decide", params={"tok": tok, "d": "approve"})
    assert r.status_code == 200
    assert "expired" in r.text.lower()
    assert client.fake_sessions.executed == []          # never ran

    # freeze must be cleared even though decide only ever saw an
    # already-terminal row — no permanent brick
    r2 = client.post("/session/psess/goto", json={"url": "https://example.com"})
    assert r2.json()["success"] is True


# ── Finding 2 (round 2 review): back is a mutating op too ──────────────────

def test_back_frozen_while_approval_pending(client):
    """'back' must be subject to the pending-approval freeze exactly like
    goto/click/type/press/scroll — otherwise an agent can navigate backward
    between a gated request and its approval, drifting the state the
    approval was described against."""
    aid = client.post("/session/psess/click", json={"index": 3}).json()["approval_id"]

    r_back = client.post("/session/psess/back", json={})
    assert r_back.json()["success"] is False
    assert "approval pending" in r_back.json()["error"]
    assert client.fake_sessions.executed == []

    from browser import db
    tok = db.get_approval(aid)["token"]
    client.get(f"/approvals/{aid}/decide", params={"tok": tok, "d": "deny"})

    # session usable again once the approval is decided
    r_back2 = client.post("/session/psess/back", json={})
    assert r_back2.json()["success"] is True


# ── Finding 3 (round 2 review): click-nav to a mutation URL ────────────────

def test_click_nav_href_gated_despite_neutral_text(client, monkeypatch):
    """A neutral-text link ('Manage preferences') to a mutation URL
    (/unsubscribe?token=…) must gate even though is_gated_click's text/attr
    heuristic alone misses it — Playwright's click-navigation never routes
    through session_goto/is_gated_goto on its own, so href has to feed the
    click-gating decision directly."""
    async def fake_element_info(sid, index):
        return {"tag": "a", "type": "", "text": "Manage", "in_form": False,
                "form_method": "", "href": "/unsubscribe?token=x"}
    monkeypatch.setattr(client.fake_sessions, "element_info", fake_element_info)

    r = client.post("/session/psess/click", json={"index": 3})
    body = r.json()
    assert body["status"] == "pending_approval"
    assert client.fake_sessions.executed == []

    from browser import db
    approval = db.get_approval(body["approval_id"])
    action = json.loads(approval["action"])
    assert action["descriptor"]["href"] == "/unsubscribe?token=x"


def test_click_nav_href_anonymous_not_gated(client, monkeypatch):
    """The identical neutral-text mutation-URL link in an anonymous session
    must execute straight through — the write gate only applies to
    logged-in profile sessions."""
    async def fake_element_info(sid, index):
        return {"tag": "a", "type": "", "text": "Manage", "in_form": False,
                "form_method": "", "href": "/unsubscribe?token=x"}
    monkeypatch.setattr(client.fake_sessions, "element_info", fake_element_info)

    r = client.post("/session/asess/click", json={"index": 3})
    assert r.json()["success"] is True
    assert ("asess", "click", {"index": 3}) in client.fake_sessions.executed


def test_click_nav_plain_href_not_gated(client, monkeypatch):
    """A link with no query string and no mutation verb in text or href
    (/about) must stay ungated — plain browsing in a profile session
    shouldn't trip the gate."""
    async def fake_element_info(sid, index):
        return {"tag": "a", "type": "", "text": "About", "in_form": False,
                "form_method": "", "href": "/about"}
    monkeypatch.setattr(client.fake_sessions, "element_info", fake_element_info)

    r = client.post("/session/psess/click", json={"index": 3})
    assert r.json()["success"] is True
    assert ("psess", "click", {"index": 3}) in client.fake_sessions.executed


# ── Finding 3 (round 2 review): press gets the same drift protection ───────

def test_press_approve_executes_when_element_unchanged(client, monkeypatch):
    """Happy path for the press drift-check: if the active element at decide
    time still matches what was captured at gate time, the approved
    keypress executes normally."""
    active = {"tag": "input", "type": "text", "text": "Submit order",
              "in_form": True, "form_method": "post"}

    async def fake_active(sid):
        return dict(active)
    monkeypatch.setattr(client.fake_sessions, "active_element", fake_active)

    aid = client.post("/session/psess/press", json={"key": "Enter"}).json()["approval_id"]
    from browser import db
    tok = db.get_approval(aid)["token"]
    r = client.get(f"/approvals/{aid}/decide", params={"tok": tok, "d": "approve"})
    assert r.status_code == 200
    assert db.get_approval(aid)["status"] == "executed"
    assert ("psess", "press", {"key": "Enter"}) in client.fake_sessions.executed


def test_press_element_drift_refuses_execution(client, monkeypatch):
    """The approval was described against the active element captured at
    gate time ('Submit order' input). If a different element is focused by
    the time Serge approves, the replay must refuse — same protection click
    already has (finding 2b) — rather than press Enter on whatever now has
    focus."""
    async def fake_active_at_gate(sid):
        return {"tag": "input", "type": "text", "text": "Submit order",
                "in_form": True, "form_method": "post"}
    monkeypatch.setattr(client.fake_sessions, "active_element", fake_active_at_gate)

    aid = client.post("/session/psess/press", json={"key": "Enter"}).json()["approval_id"]

    async def fake_active_at_decide(sid):
        return {"tag": "textarea", "type": "", "text": "Different field",
                "in_form": True, "form_method": "post"}
    monkeypatch.setattr(client.fake_sessions, "active_element", fake_active_at_decide)

    from browser import db
    tok = db.get_approval(aid)["token"]
    r = client.get(f"/approvals/{aid}/decide", params={"tok": tok, "d": "approve"})
    assert "element changed" in r.text.lower()
    assert client.fake_sessions.executed == []          # NOT executed
    assert db.get_approval(aid)["status"] == "expired"

    # freeze must be cleared too, same as the click-drift case
    r2 = client.post("/session/psess/goto", json={"url": "https://example.com"})
    assert r2.json()["success"] is True
