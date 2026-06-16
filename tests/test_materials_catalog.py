"""Materials catalog picker — CRUD + receipt-suggestion endpoints (Method 4)."""
import importlib
import json
import os
import sqlite3
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)


@pytest.fixture
def app_module():
    if "dashboard.app" in sys.modules:
        del sys.modules["dashboard.app"]
    mod = importlib.import_module("dashboard.app")
    mod.app.config["TESTING"] = True
    return mod


@pytest.fixture
def temp_ahb_db(app_module, tmp_path, monkeypatch):
    """Redirect _ahb_db() to a fresh temp DB with materials + receipts tables."""
    dbp = str(tmp_path / "mat.db")
    conn = sqlite3.connect(dbp)
    app_module._ensure_materials_catalog(conn)  # creates + seeds catalog
    conn.execute("""CREATE TABLE ahb_receipts (
        id TEXT PRIMARY KEY, vendor TEXT, store_name TEXT, category TEXT,
        receipt_date TEXT, items_json TEXT)""")
    conn.commit()
    conn.close()

    def _factory():
        c = sqlite3.connect(dbp, timeout=30.0)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(app_module, "_ahb_db", _factory)
    return dbp


@pytest.fixture
def client(app_module, temp_ahb_db):
    return app_module.app.test_client()


def test_seed_populates_home_depot(app_module, tmp_path):
    conn = sqlite3.connect(str(tmp_path / "seed.db"))
    app_module._ensure_materials_catalog(conn)
    n = conn.execute("SELECT COUNT(*) FROM ahb_materials_catalog").fetchone()[0]
    hd = conn.execute(
        "SELECT COUNT(*) FROM ahb_materials_catalog WHERE vendor='Home Depot'").fetchone()[0]
    cats = conn.execute(
        "SELECT COUNT(DISTINCT category) FROM ahb_materials_catalog").fetchone()[0]
    conn.close()
    assert n >= 120
    assert hd >= 100   # the bulk of the seed is Home Depot
    assert cats >= 10  # categories drive the picker's category nav


def test_seed_is_idempotent_and_tops_up(app_module, tmp_path, monkeypatch):
    dbp = str(tmp_path / "topup.db")
    conn = sqlite3.connect(dbp)
    monkeypatch.setattr(app_module, "_MATERIALS_SEED",
                        [("Home Depot", "Alpha", "each", 1.0, "X")])
    app_module._ensure_materials_catalog(conn)
    app_module._ensure_materials_catalog(conn)  # second pass must not duplicate
    assert conn.execute("SELECT COUNT(*) FROM ahb_materials_catalog").fetchone()[0] == 1
    # grow the seed -> the new row is added on the next pass
    monkeypatch.setattr(app_module, "_MATERIALS_SEED",
                        [("Home Depot", "Alpha", "each", 1.0, "X"),
                         ("Home Depot", "Beta", "each", 2.0, "X")])
    app_module._ensure_materials_catalog(conn)
    names = [r[0] for r in conn.execute(
        "SELECT name FROM ahb_materials_catalog ORDER BY name").fetchall()]
    conn.close()
    assert names == ["Alpha", "Beta"]


def test_seed_does_not_resurrect_deleted(app_module, tmp_path, monkeypatch):
    dbp = str(tmp_path / "del.db")
    conn = sqlite3.connect(dbp)
    monkeypatch.setattr(app_module, "_MATERIALS_SEED",
                        [("Home Depot", "Alpha", "each", 1.0, "X")])
    app_module._ensure_materials_catalog(conn)
    conn.execute("UPDATE ahb_materials_catalog SET active=0 WHERE name='Alpha'")
    conn.commit()
    app_module._ensure_materials_catalog(conn)  # must NOT re-add a soft-deleted seed row
    n = conn.execute(
        "SELECT COUNT(*) FROM ahb_materials_catalog WHERE name='Alpha'").fetchone()[0]
    conn.close()
    assert n == 1


def test_list_returns_seeded_rows(client):
    r = client.get("/api/ahb/estimator/materials")
    assert r.status_code == 200
    rows = r.get_json()
    assert isinstance(rows, list) and len(rows) >= 50
    assert all("vendor" in x and "name" in x and "unit_price" in x for x in rows)


def test_create_update_delete(client):
    r = client.post("/api/ahb/estimator/materials",
                    json={"vendor": "Amazon", "name": "Test Widget", "unit": "each", "unit_price": 9.5})
    assert r.status_code == 200 and r.get_json()["success"] is True
    mid = r.get_json()["id"]
    r = client.post("/api/ahb/estimator/materials",
                    json={"id": mid, "vendor": "Amazon", "name": "Test Widget 2", "unit_price": 12})
    assert r.get_json()["success"] is True
    rows = client.get("/api/ahb/estimator/materials").get_json()
    assert any(x["id"] == mid and x["name"] == "Test Widget 2" for x in rows)
    r = client.delete(f"/api/ahb/estimator/materials/{mid}")
    assert r.get_json()["success"] is True
    rows = client.get("/api/ahb/estimator/materials").get_json()
    assert not any(x["id"] == mid for x in rows)


def test_create_requires_name(client):
    r = client.post("/api/ahb/estimator/materials", json={"vendor": "Amazon", "name": "  "})
    assert r.status_code == 400
    assert r.get_json()["success"] is False


def _seed_receipt(dbp, rid, vendor, items, date="2026-04-01", category="Materials"):
    c = sqlite3.connect(dbp)
    c.execute(
        "INSERT INTO ahb_receipts (id,vendor,store_name,category,receipt_date,items_json) VALUES (?,?,?,?,?,?)",
        (rid, vendor, vendor, category, date, json.dumps(items)))
    c.commit()
    c.close()


def test_suggest_groups_and_picks_latest_price(client, temp_ahb_db):
    _seed_receipt(temp_ahb_db, "r1", "Home Depot",
                  [{"name": "2x4x8 STUD", "price": 3.98}], date="2026-01-01")
    _seed_receipt(temp_ahb_db, "r2", "Home Depot",
                  [{"name": "2x4x8 STUD", "price": 4.25}, {"name": "DECK SCREWS 5LB", "price": 28}],
                  date="2026-05-01")
    r = client.get("/api/ahb/estimator/material-suggest?vendor=Home Depot")
    assert r.status_code == 200
    out = r.get_json()
    stud = [x for x in out if x["name"] == "2x4x8 STUD"][0]
    assert stud["freq"] == 2
    assert stud["last_price"] == 4.25  # most recent date wins
    assert any(x["name"] == "DECK SCREWS 5LB" for x in out)


def test_suggest_filters_by_vendor_and_q(client, temp_ahb_db):
    _seed_receipt(temp_ahb_db, "r1", "Home Depot", [{"name": "PVC PIPE 10FT", "price": 8}])
    _seed_receipt(temp_ahb_db, "r2", "Lowe's", [{"name": "PVC PIPE 10FT", "price": 7.5}])
    only_hd = client.get("/api/ahb/estimator/material-suggest?vendor=Home Depot").get_json()
    assert all(x["vendor"] == "Home Depot" for x in only_hd)
    q = client.get("/api/ahb/estimator/material-suggest?q=pvc").get_json()
    assert q and all("pvc" in x["name"].lower() for x in q)


def test_suggest_vendor_match_ignores_the_prefix(client, temp_ahb_db):
    # Receipts store "The Home Depot"; the catalog/dropdown use "Home Depot".
    _seed_receipt(temp_ahb_db, "r1", "The Home Depot", [{"name": "WALL CABINET 30IN", "price": 119}])
    out = client.get("/api/ahb/estimator/material-suggest?vendor=Home Depot").get_json()
    assert any(x["name"] == "WALL CABINET 30IN" for x in out)


def test_suggest_drops_generic_placeholder_names(client, temp_ahb_db):
    _seed_receipt(temp_ahb_db, "r1", "Home Depot",
                  [{"name": "Item 1", "price": 9.99}, {"name": "Line 2", "price": 5},
                   {"name": "REAL PRODUCT NAME", "price": 12}])
    out = client.get("/api/ahb/estimator/material-suggest?vendor=Home Depot").get_json()
    names = [x["name"] for x in out]
    assert "REAL PRODUCT NAME" in names
    assert "Item 1" not in names and "Line 2" not in names


def test_price_sync_preview_proposes_from_matching_receipt(client, temp_ahb_db):
    mid = client.post("/api/ahb/estimator/materials",
                      json={"vendor": "Home Depot", "name": "2x4x8 Stud Special",
                            "unit": "each", "unit_price": 3.00}).get_json()["id"]
    _seed_receipt(temp_ahb_db, "r1", "The Home Depot",
                  [{"name": "2X4X8 STUD SPECIAL -A-", "price": 4.25}], date="2026-05-01")
    props = client.get("/api/ahb/estimator/material-price-sync").get_json()
    p = [x for x in props if x["id"] == mid]
    assert p, "expected a proposal for the matching catalog row"
    assert p[0]["new_price"] == 4.25 and p[0]["old_price"] == 3.0


def test_price_sync_skips_low_overlap(client, temp_ahb_db):
    client.post("/api/ahb/estimator/materials",
                json={"vendor": "Home Depot", "name": "Toilet Wax Ring Premium",
                      "unit": "each", "unit_price": 4.48})
    _seed_receipt(temp_ahb_db, "r1", "Home Depot", [{"name": "RING", "price": 99}])
    props = client.get("/api/ahb/estimator/material-price-sync").get_json()
    assert not any(x["name"] == "Toilet Wax Ring Premium" for x in props)


def test_price_sync_skips_implausible_price_jump(client, temp_ahb_db):
    # An ~18x jump is almost certainly a line-total / wrong-product match, not drift.
    client.post("/api/ahb/estimator/materials",
                json={"vendor": "Home Depot", "name": "Foo Bar Baz Widget",
                      "unit": "each", "unit_price": 5.00})
    _seed_receipt(temp_ahb_db, "r1", "Home Depot",
                  [{"name": "FOO BAR BAZ WIDGET", "price": 99.00}])
    props = client.get("/api/ahb/estimator/material-price-sync").get_json()
    assert not any(x["name"] == "Foo Bar Baz Widget" for x in props)


def test_price_sync_vendor_scoped(client, temp_ahb_db):
    client.post("/api/ahb/estimator/materials",
                json={"vendor": "Home Depot", "name": "Special Widget XYZ",
                      "unit": "each", "unit_price": 2.00})
    _seed_receipt(temp_ahb_db, "r1", "Lowe's", [{"name": "SPECIAL WIDGET XYZ", "price": 9.99}])
    props = client.get("/api/ahb/estimator/material-price-sync").get_json()
    assert not any(x["name"] == "Special Widget XYZ" for x in props)


def test_price_sync_apply_updates_prices(client, temp_ahb_db):
    mid = client.post("/api/ahb/estimator/materials",
                      json={"vendor": "Home Depot", "name": "PVC Pipe Sync Test",
                            "unit": "each", "unit_price": 5.00}).get_json()["id"]
    r = client.post("/api/ahb/estimator/material-price-sync",
                    json={"updates": [{"id": mid, "new_price": 8.50}]})
    assert r.get_json()["updated"] == 1
    rows = client.get("/api/ahb/estimator/materials").get_json()
    assert any(x["id"] == mid and x["unit_price"] == 8.5 for x in rows)


def test_suggest_tolerates_bad_items_json(client, temp_ahb_db):
    c = sqlite3.connect(temp_ahb_db)
    c.execute("INSERT INTO ahb_receipts (id,vendor,store_name,category,receipt_date,items_json) VALUES (?,?,?,?,?,?)",
              ("bad1", "Home Depot", "Home Depot", "Materials", "2026-03-01", "not json"))
    c.execute("INSERT INTO ahb_receipts (id,vendor,store_name,category,receipt_date,items_json) VALUES (?,?,?,?,?,?)",
              ("empty1", "Home Depot", "Home Depot", "Materials", "2026-03-01", ""))
    c.commit()
    c.close()
    r = client.get("/api/ahb/estimator/material-suggest?vendor=Home Depot")
    assert r.status_code == 200
    assert isinstance(r.get_json(), list)  # no 500
