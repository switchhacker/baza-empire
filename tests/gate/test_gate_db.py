import numpy as np
import pytest
from gate import gate_db


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("BAZA_GATE_DB", str(tmp_path / "gate.db"))
    gate_db.init_db()


def _emb(seed):
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(512).astype(np.float32)
    return v / np.linalg.norm(v)


def test_add_face_and_list_gallery():
    gate_db.add_face("serge", "login_unlock", _emb(1))
    gate_db.add_face("serge", "door", _emb(1))
    rows = gate_db.gallery()
    assert len(rows) == 1
    assert rows[0]["person"] == "serge"
    assert set(rows[0]["roles"]) == {"login_unlock", "door"}


def test_gallery_embeddings_roundtrip_preserves_vector():
    v = _emb(2)
    gate_db.add_face("ana", "door", v)
    embs = gate_db.gallery_embeddings(role="door")
    assert len(embs) == 1
    person, role, got = embs[0]
    assert person == "ana" and role == "door"
    np.testing.assert_allclose(got, v, rtol=1e-6)


def test_delete_person_removes_all_roles():
    gate_db.add_face("temp", "door", _emb(3))
    gate_db.add_face("temp", "login_unlock", _emb(3))
    n = gate_db.delete_person("temp")
    assert n == 2
    assert gate_db.gallery() == []


def test_log_and_read_events_newest_first():
    gate_db.log_event(node="baza-gate-cam-01", kind="grant", verdict="grant",
                       person="serge", score=0.72)
    gate_db.log_event(node="baza-gate-cam-01", kind="deny", verdict="deny",
                       score=0.10)
    ev = gate_db.recent_events(limit=10)
    assert len(ev) == 2
    assert ev[0]["kind"] == "deny"   # newest first
    assert ev[1]["person"] == "serge"


def test_gallery_embeddings_no_role_filter_returns_all():
    gate_db.add_face("p1", "door", _emb(4))
    gate_db.add_face("p1", "login_unlock", _emb(5))
    embs = gate_db.gallery_embeddings()          # role=None
    assert len(embs) == 2
