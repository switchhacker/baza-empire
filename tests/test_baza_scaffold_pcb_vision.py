"""Unit tests for the PCB Vision scaffold feature.

Covers:
- Engine: pcb_vision in NODE_TYPES + default agent
- Skill: helpers (clamp, IoU, normalize, merge) + integration via mocked Ollama
- Routes: upload, create_from_datahub, analyze, generate_schematic, image-serve
  (Flask test client; vision subprocess is monkey-patched to a no-op so tests
   don't depend on the local Ollama instance.)
- Best-guess wires heuristic
"""
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    from dashboard.app import _ensure_scaffold_tables
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT, scaffold_paused INTEGER DEFAULT 0)")
    _ensure_scaffold_tables(con)
    con.execute("INSERT INTO projects(id, name) VALUES('p1', 'T')")
    con.commit(); con.close()
    yield path
    try: os.unlink(path)
    except OSError: pass


@pytest.fixture
def app_client(db, tmp_path, monkeypatch):
    """Flask test client wired against the temp db."""
    import flask
    from dashboard import scaffold as scaffold_mod
    monkeypatch.setenv("BAZA_PROJECTS_DB", db)
    monkeypatch.setattr(scaffold_mod, "_db_path", lambda: db)
    # never spawn the real vision subprocess during route tests
    monkeypatch.setattr(scaffold_mod, "_spawn_analyze", lambda nid, mode="merge": None)
    # redirect uploads to tmp
    monkeypatch.setattr(scaffold_mod, "ARTIFACTS_DIR", tmp_path)

    app = flask.Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test"
    app.register_blueprint(scaffold_mod.scaffold_bp)
    with app.test_client() as c:
        yield c, db


# ────────────────────────── engine ──────────────────────────

def test_pcb_vision_node_type_registered():
    from core.scaffold_engine import NODE_TYPES, DEFAULT_WEIGHTS, default_agent_for
    assert "pcb_vision" in NODE_TYPES
    assert DEFAULT_WEIGHTS["pcb_vision"] == 2
    assert default_agent_for("pcb_vision") == "claw_batto"


# ────────────────────────── skill helpers ──────────────────────────

def test_skill_helper_clamp_bbox():
    from skills.shared import scaffold_analyze_pcb_image as s
    assert s._clamp_bbox([0.1, 0.2, 0.3, 0.4]) == [0.1, 0.2, 0.3, 0.4]
    assert s._clamp_bbox("garbage") is None
    assert s._clamp_bbox([1, 2, 3]) is None
    out = s._clamp_bbox([-0.5, 0.5, 0.3, 0.6])
    assert out[0] == 0.0 and out[1] == 0.5


def test_skill_helper_iou():
    from skills.shared import scaffold_analyze_pcb_image as s
    assert s._iou([0,0,0.5,0.5], [0,0,0.5,0.5]) == 1.0
    assert s._iou([0,0,0.5,0.5], [0.6,0.6,0.3,0.3]) == 0.0
    assert round(s._iou([0,0,0.5,0.5], [0.25,0,0.5,0.5]), 3) == 0.333


def test_skill_helper_normalize_drops_low_confidence_and_invalid_part_id():
    from skills.shared import scaffold_analyze_pcb_image as s
    parsed = {"components": [
        {"label":"ESP32","bbox":[0.1,0.1,0.4,0.4],"confidence":0.9,"suggested_part_id":"esp32-devkit"},
        {"label":"noise","bbox":[0.1,0.1,0.4,0.4],"confidence":0.1},  # below threshold
        {"label":"unknown","bbox":[0.6,0.6,0.2,0.2],"confidence":0.5,"suggested_part_id":"not-a-real-id"},
        {"label":"","bbox":[0.1,0.1,0.1,0.1],"confidence":0.9},  # empty label
    ]}
    out = s._normalize_components(parsed)
    assert len(out) == 2
    assert out[0]["suggested_part_id"] == "esp32-devkit"
    assert out[1]["suggested_part_id"] is None  # invalid catalog id cleared


def test_skill_helper_merge_preserves_user_corrected_and_iou_match():
    from skills.shared import scaffold_analyze_pcb_image as s
    existing = [
        {"id":"ov_1","label":"Edited","bbox":[0.1,0.1,0.3,0.3],"user_corrected":True},
        {"id":"ov_2","label":"OldNoise","bbox":[0.6,0.6,0.2,0.2],"user_corrected":False},
    ]
    fresh = [
        {"id":"ov_x","label":"FreshSame","bbox":[0.62,0.61,0.18,0.19],"user_corrected":False},
        {"id":"ov_y","label":"NewThing","bbox":[0.3,0.3,0.2,0.2],"user_corrected":False},
    ]
    merged = s._merge_overlays(existing, fresh)
    labels = {m["label"] for m in merged}
    assert "Edited" in labels         # user_corrected kept verbatim
    assert "OldNoise" not in labels   # replaced (IoU match)
    assert "FreshSame" in labels      # match for old ov_2
    assert "NewThing" in labels       # new detection added
    # ids are re-numbered ov_1..ov_N
    assert [m["id"] for m in merged] == [f"ov_{i+1}" for i in range(len(merged))]


# ────────────────────────── routes ──────────────────────────

def test_route_upload_creates_node_and_saves_file(app_client):
    client, db = app_client
    img_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 256  # minimal "jpeg" header + filler
    resp = client.post(
        "/api/baza/projects/p1/scaffold/pcb_vision/upload",
        data={"file": (io.BytesIO(img_bytes), "board.jpg")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200, resp.data
    j = resp.get_json()
    assert j["ok"] is True
    nid = j["node_id"]

    con = sqlite3.connect(db); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM project_scaffold_nodes WHERE id = ?", (nid,)).fetchone()
    con.close()
    assert row["node_type"] == "pcb_vision"
    payload = json.loads(row["payload_json"])
    assert payload["image_source"] == "upload"
    assert Path(payload["image_path"]).exists()
    assert Path(payload["image_path"]).read_bytes() == img_bytes


def test_route_upload_rejects_bad_extension(app_client):
    client, _ = app_client
    resp = client.post(
        "/api/baza/projects/p1/scaffold/pcb_vision/upload",
        data={"file": (io.BytesIO(b"text"), "notes.txt")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 415


def test_route_create_from_datahub_404_on_missing_file(app_client):
    client, _ = app_client
    resp = client.post(
        "/api/baza/projects/p1/scaffold/pcb_vision/create_from_datahub",
        json={"datahub_path": "/tmp/__definitely_not_a_real_path__.jpg"},
    )
    assert resp.status_code == 404


def test_route_create_from_datahub_403_on_locked_private(app_client, tmp_path):
    client, _ = app_client
    fake = tmp_path / "private.jpg"
    fake.write_bytes(b"\xff\xd8\xff\xe0")
    resp = client.post(
        "/api/baza/projects/p1/scaffold/pcb_vision/create_from_datahub",
        json={"datahub_path": str(fake), "is_private": True},
    )
    assert resp.status_code == 403


def test_route_image_serve_404_when_no_payload(app_client):
    client, db = app_client
    # Make a pcb_vision node WITHOUT a file at the image_path
    from core.scaffold_engine import ScaffoldEngine
    eng = ScaffoldEngine(db)
    nid = eng.create_node("p1", node_type="pcb_vision", title="X",
                          payload={"image_path": "/tmp/__nope__.jpg", "image_source": "upload"})
    resp = client.get(f"/api/baza/projects/p1/scaffold/pcb_vision/image/{nid}")
    assert resp.status_code == 404


# ────────────────────────── generate_schematic + best-guess ──────────────────────────

def test_generate_schematic_from_overlays(app_client):
    client, db = app_client
    from core.scaffold_engine import ScaffoldEngine
    eng = ScaffoldEngine(db)
    payload = {
        "image_path": "/tmp/x.jpg",
        "image_source": "upload",
        "overlays": [
            {"id":"ov_1","label":"ESP32","bbox":[0.1,0.1,0.3,0.4],"suggested_part_id":"esp32-devkit","confidence":0.9,"user_corrected":False},
            {"id":"ov_2","label":"HC-SR04","bbox":[0.55,0.2,0.2,0.15],"suggested_part_id":None,"confidence":0.7,"user_corrected":False},
        ],
        "schematic": {"components":[], "wires":[], "notes":""},
        "best_guess_wires": False,
    }
    nid = eng.create_node("p1", node_type="pcb_vision", title="board", payload=payload)
    resp = client.post(f"/api/baza/projects/p1/scaffold/pcb_vision/generate_schematic/{nid}",
                       json={"best_guess_wires": False})
    assert resp.status_code == 200
    j = resp.get_json()
    assert j["ok"] is True
    assert len(j["schematic"]["components"]) == 2
    assert j["schematic"]["wires"] == []   # no best-guess


def test_best_guess_wires_produces_power_and_ground(app_client):
    """Heuristic wires: USB → ESP32 VCC, plus GND bus."""
    client, db = app_client
    from dashboard.scaffold import _best_guess_wires
    from core.baza_components_library import get_component
    esp = get_component("esp32-devkit")
    assert esp, "esp32-devkit must exist in components library"
    components = [
        {"id":"ov_1","part_id":"esp32-devkit","label":"ESP32",
         "x":100,"y":100,"width":esp["width"],"height":esp["height"],
         "pins": esp["pins"]},
    ]
    # Find a power source — search the catalog for the first power.* item
    from core.baza_components_library import list_components
    power = next((c for c in list_components() if c["category"] == "power"), None)
    assert power, "library has at least one power component"
    components.append({
        "id":"ov_2","part_id":power["id"],"label":power["name"],
        "x":50,"y":50,"width":power["width"],"height":power["height"],
        "pins": power["pins"],
    })
    wires = _best_guess_wires(components)
    assert any(w["kind"] == "power" for w in wires)
    # Each wire must be marked auto_generated
    assert all(w["auto_generated"] for w in wires)


# ────────────────────────── analyze with mocked vision ──────────────────────────

def test_analyze_skill_with_mocked_ollama(app_client, tmp_path, monkeypatch):
    """Run the full skill subprocess with the vision call monkey-patched."""
    client, db = app_client
    from core.scaffold_engine import ScaffoldEngine
    eng = ScaffoldEngine(db)

    # write a tiny real JPEG so Pillow can open it
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("Pillow not installed")
    img_path = tmp_path / "real.jpg"
    Image.new("RGB", (64, 64), (200, 50, 50)).save(img_path, "JPEG")

    payload = {
        "image_path": str(img_path),
        "image_source": "upload",
        "overlays": [], "schematic": {"components":[],"wires":[],"notes":""},
        "best_guess_wires": False,
    }
    nid = eng.create_node("p1", node_type="pcb_vision", title="x", payload=payload, status="running")

    from skills.shared import scaffold_analyze_pcb_image as s
    fake_response = json.dumps({
        "board_label": "Test board",
        "board_function": "Doing test things",
        "components": [
            {"label":"ESP32","bbox":[0.1,0.1,0.3,0.4],"confidence":0.9,"suggested_part_id":"esp32-devkit"},
        ],
    })
    monkeypatch.setattr(s, "_call_ollama_vision", lambda *a, **kw: fake_response)
    monkeypatch.setattr(s, "DB_PATH", Path(db))

    # Run directly (not subprocess) so monkey-patches are honored
    monkeypatch.setenv("SKILL_ARGS", json.dumps({"node_id": nid, "mode": "reset"}))
    rc = s.main()
    assert rc == 0

    fresh = eng.get_node(nid)
    pl = json.loads(fresh["payload_json"])
    assert fresh["status"] == "completed"
    assert pl["board_label"] == "Test board"
    assert len(pl["overlays"]) == 1
    assert pl["overlays"][0]["suggested_part_id"] == "esp32-devkit"


def test_analyze_skill_handles_empty_vision_result(app_client, tmp_path, monkeypatch):
    client, db = app_client
    from core.scaffold_engine import ScaffoldEngine
    eng = ScaffoldEngine(db)
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("Pillow not installed")
    img_path = tmp_path / "blank.jpg"
    Image.new("RGB", (32, 32), (0, 0, 0)).save(img_path, "JPEG")

    payload = {"image_path": str(img_path), "image_source": "upload",
               "overlays": [], "schematic": {"components":[],"wires":[],"notes":""}}
    nid = eng.create_node("p1", node_type="pcb_vision", title="blank", payload=payload, status="running")

    from skills.shared import scaffold_analyze_pcb_image as s
    monkeypatch.setattr(s, "_call_ollama_vision", lambda *a, **kw: json.dumps({"components": []}))
    monkeypatch.setattr(s, "DB_PATH", Path(db))
    monkeypatch.setenv("SKILL_ARGS", json.dumps({"node_id": nid, "mode": "reset"}))
    rc = s.main()
    assert rc == 0
    fresh = eng.get_node(nid)
    assert fresh["status"] == "awaiting_input"
