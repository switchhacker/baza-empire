import base64
from io import BytesIO

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from gate import routes, gate_db, unlock_token


def _jpeg_b64():
    buf = BytesIO()
    Image.new("RGB", (16, 16), "white").save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode()


def _unit(seed):
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(512).astype(np.float32)
    return v / np.linalg.norm(v)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BAZA_GATE_DB", str(tmp_path / "gate.db"))
    monkeypatch.setenv("BAZA_GATE_HMAC_SECRET", "test-secret")
    gate_db.init_db()
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def test_enroll_then_gallery_lists_person(client, monkeypatch):
    monkeypatch.setattr(routes.face_recognizer, "embed", lambda b: [_unit(1)])
    r = client.post("/edge/gate/enroll", json={
        "person": "serge", "roles": ["login_unlock", "door"],
        "images": [_jpeg_b64()],
    })
    assert r.status_code == 200
    assert r.json()["n_embeddings"] == 2  # one image x two roles
    g = client.get("/edge/gate/gallery").json()
    assert g[0]["person"] == "serge"


def test_capture_grant_returns_valid_token_and_unlocks(client, monkeypatch):
    serge = _unit(1)
    monkeypatch.setattr(routes.face_recognizer, "embed", lambda b: [serge])
    unlocked = {"n": 0}
    monkeypatch.setattr(routes.session_unlock, "unlock_session",
                        lambda: unlocked.__setitem__("n", unlocked["n"] + 1) or True)
    published = []
    monkeypatch.setattr(routes, "_publish_auth", lambda person: published.append(person))
    gate_db.add_face("serge", "door", serge)
    gate_db.add_face("serge", "login_unlock", serge)

    nonce = unlock_token.new_nonce()
    r = client.post("/edge/gate/capture", json={
        "node_id": "baza-gate-cam-01", "nonce": nonce, "image": _jpeg_b64(),
    })
    body = r.json()
    assert body["verdict"] == "grant"
    assert body["person"] == "serge"
    assert unlock_token.verify(nonce, body["token"]) is True
    assert unlocked["n"] == 1
    assert published == ["serge"]


def test_capture_deny_no_token(client, monkeypatch):
    gate_db.add_face("serge", "door", _unit(1))
    monkeypatch.setattr(routes.face_recognizer, "embed", lambda b: [_unit(99)])
    nonce = unlock_token.new_nonce()
    r = client.post("/edge/gate/capture", json={
        "node_id": "baza-gate-cam-01", "nonce": nonce, "image": _jpeg_b64(),
    })
    body = r.json()
    assert body["verdict"] == "deny"
    assert body.get("token") in (None, "")


def test_capture_no_face_denies(client, monkeypatch):
    gate_db.add_face("serge", "door", _unit(1))
    monkeypatch.setattr(routes.face_recognizer, "embed", lambda b: [])
    r = client.post("/edge/gate/capture", json={
        "node_id": "baza-gate-cam-01", "nonce": unlock_token.new_nonce(),
        "image": _jpeg_b64(),
    })
    assert r.json()["verdict"] == "deny"


def test_capture_denies_on_recognition_error(client, monkeypatch):
    # A degenerate (NaN) embedding makes best_match raise; route must fail-closed.
    gate_db.add_face("serge", "door", _unit(1))
    monkeypatch.setattr(routes.face_recognizer, "embed",
                        lambda b: [np.full(512, np.nan, dtype=np.float32)])
    r = client.post("/edge/gate/capture", json={
        "node_id": "n1", "nonce": unlock_token.new_nonce(), "image": _jpeg_b64(),
    })
    body = r.json()
    assert body["verdict"] == "deny"
    assert body.get("token") in (None, "")
    # a security event should have been logged
    kinds = [e["kind"] for e in gate_db.recent_events(limit=10)]
    assert "security" in kinds


def test_delete_person(client, monkeypatch):
    gate_db.add_face("temp", "door", _unit(5))
    r = client.delete("/edge/gate/gallery/temp")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert client.get("/edge/gate/gallery").json() == []


def test_events_endpoint_returns_recent(client, monkeypatch):
    gate_db.add_face("serge", "door", _unit(1))
    monkeypatch.setattr(routes.face_recognizer, "embed", lambda b: [_unit(99)])
    client.post("/edge/gate/capture", json={
        "node_id": "n1", "nonce": unlock_token.new_nonce(), "image": _jpeg_b64(),
    })
    ev = client.get("/edge/gate/events?limit=5").json()
    assert len(ev) >= 1
    assert ev[0]["kind"] in ("deny", "grant")


def test_capture_denies_on_bad_nonce(client, monkeypatch):
    # Face matches, but the nonce isn't valid hex -> must be a clean deny, not a 500.
    serge = _unit(1)
    monkeypatch.setattr(routes.face_recognizer, "embed", lambda b: [serge])
    gate_db.add_face("serge", "door", serge)
    r = client.post("/edge/gate/capture", json={
        "node_id": "n1", "nonce": "not-hex!!", "image": _jpeg_b64(),
    })
    body = r.json()
    assert r.status_code == 200
    assert body["verdict"] == "deny"
    assert body.get("token") in (None, "")
    kinds = [e["kind"] for e in gate_db.recent_events(limit=10)]
    assert "security" in kinds


def test_capture_denies_on_embed_error(client, monkeypatch):
    def _raise(_):
        raise RuntimeError("camera error")
    monkeypatch.setattr(routes.face_recognizer, "embed", _raise)
    r = client.post("/edge/gate/capture", json={
        "node_id": "n1", "nonce": unlock_token.new_nonce(), "image": _jpeg_b64(),
    })
    body = r.json()
    assert r.status_code == 200
    assert body["verdict"] == "deny"
    assert body.get("token") in (None, "")
    kinds = [e["kind"] for e in gate_db.recent_events(limit=10)]
    assert "security" in kinds


def test_enroll_bad_image_returns_clean_error(client, monkeypatch):
    def _raise(_):
        raise RuntimeError("bad image")
    monkeypatch.setattr(routes.face_recognizer, "embed", _raise)
    r = client.post("/edge/gate/enroll", json={
        "person": "x", "roles": ["door"], "images": [_jpeg_b64()],
    })
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_capture_actions_reflect_enrolled_roles_door_only(client, monkeypatch):
    serge = _unit(1)
    monkeypatch.setattr(routes.face_recognizer, "embed", lambda b: [serge])
    called = {"n": 0}
    monkeypatch.setattr(routes.session_unlock, "unlock_session",
                        lambda: called.__setitem__("n", called["n"] + 1) or True)
    monkeypatch.setattr(routes, "_publish_auth", lambda p: None)
    gate_db.add_face("serge", "door", serge)   # door only, NOT login_unlock
    r = client.post("/edge/gate/capture", json={
        "node_id": "n1", "nonce": unlock_token.new_nonce(), "image": _jpeg_b64(),
    })
    body = r.json()
    assert body["verdict"] == "grant"
    assert body["actions"] == ["door"]
    assert called["n"] == 0   # login session NOT unlocked for a door-only enrollee
