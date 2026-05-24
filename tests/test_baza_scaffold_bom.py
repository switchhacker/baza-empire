import os
import sys
import sqlite3
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture
def client(monkeypatch, tmp_path):
    path = str(tmp_path / "test.db")
    monkeypatch.setenv("BAZA_PROJECTS_DB", path)
    if "dashboard.app" in sys.modules:
        del sys.modules["dashboard.app"]
    if "dashboard.scaffold" in sys.modules:
        del sys.modules["dashboard.scaffold"]
    from dashboard.app import app, _ensure_scaffold_tables
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT, scaffold_paused INTEGER DEFAULT 0)")
        _ensure_scaffold_tables(con)
        con.execute("INSERT OR REPLACE INTO projects(id, name) VALUES('p1', 'Test')")
        con.commit()
    finally:
        con.close()
    app.config["TESTING"] = True
    yield app.test_client()


def test_bom_crud(client):
    r = client.post("/api/baza/projects/p1/bom",
                    json={"name": "ESP32", "qty": 2, "unit_price": 8.5,
                          "vendor": "Adafruit", "url": "https://adafruit.com/x"})
    assert r.status_code == 201
    bid = r.get_json()["id"]
    r2 = client.get("/api/baza/projects/p1/bom")
    assert len(r2.get_json()["items"]) == 1

    r3 = client.patch(f"/api/baza/projects/p1/bom/{bid}",
                      json={"status": "ordered"})
    assert r3.status_code == 200

    r4 = client.delete(f"/api/baza/projects/p1/bom/{bid}")
    assert r4.status_code == 200


def test_bom_toggle_hand_unblocks_node(client):
    nid = client.post("/api/baza/projects/p1/scaffold/node",
                      json={"node_type": "hardware_component",
                            "title": "ESP32"}).get_json()["id"]
    client.patch(f"/api/baza/projects/p1/scaffold/node/{nid}",
                 json={"status": "awaiting_part"})
    bid = client.post("/api/baza/projects/p1/bom",
                      json={"name": "ESP32", "node_id": nid}).get_json()["id"]
    r = client.post(f"/api/baza/projects/p1/bom/{bid}/toggle-hand")
    assert r.status_code == 200
    r2 = client.get("/api/baza/projects/p1/scaffold")
    n = [n for n in r2.get_json()["nodes"] if n["id"] == nid][0]
    assert n["status"] == "pending"


def test_bom_promote_inventory(client):
    bid = client.post("/api/baza/projects/p1/bom",
                      json={"name": "10kΩ resistor",
                            "qty": 100, "unit_price": 0.02}).get_json()["id"]
    r = client.post(f"/api/baza/projects/p1/bom/{bid}/promote-inventory")
    assert r.status_code == 200
    inv_id = r.get_json()["inventory_id"]
    assert inv_id > 0
    # Verify inventory row directly (GET /inventory comes in T6)
    import sqlite3, os
    con = sqlite3.connect(os.environ["BAZA_PROJECTS_DB"])
    try:
        row = con.execute("SELECT name FROM baza_inventory WHERE id=?", (inv_id,)).fetchone()
        assert row and row[0] == "10kΩ resistor"
    finally:
        con.close()


def test_bom_not_found(client):
    r = client.patch("/api/baza/projects/p1/bom/9999",
                     json={"status": "ordered"})
    assert r.status_code == 404


def test_inventory_crud(client):
    r = client.post("/api/baza/inventory",
                    json={"name": "Arduino Uno", "category": "MCU",
                          "quantity": 3, "location": "garage bin 1"})
    assert r.status_code == 201
    iid = r.get_json()["id"]
    items = client.get("/api/baza/inventory").get_json()["items"]
    assert any(i["id"] == iid for i in items)

    r2 = client.patch(f"/api/baza/inventory/{iid}", json={"quantity": 4})
    assert r2.status_code == 200

    r3 = client.delete(f"/api/baza/inventory/{iid}")
    assert r3.status_code == 200


def test_equipment_crud(client):
    r = client.post("/api/baza/equipment",
                    json={"name": "Hakko FX-888D", "type": "soldering"})
    assert r.status_code == 201
    eid = r.get_json()["id"]
    items = client.get("/api/baza/equipment").get_json()["items"]
    assert any(i["id"] == eid for i in items)

    client.patch(f"/api/baza/equipment/{eid}", json={"status": "in_use"})
    items = client.get("/api/baza/equipment").get_json()["items"]
    assert next(i for i in items if i["id"] == eid)["status"] == "in_use"

    client.delete(f"/api/baza/equipment/{eid}")


def test_inventory_not_found(client):
    r = client.patch("/api/baza/inventory/9999", json={"quantity": 1})
    assert r.status_code == 404
