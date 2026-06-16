# Materials Catalog Picker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a vendor→product→qty materials picker (backed by a local catalog + opt-in receipt suggestions) to the Custom Pricing (Method 4) Materials section of the AHB123 project modal, mirroring the existing Equipment picker.

**Architecture:** New `ahb_materials_catalog` SQLite table + CRUD endpoints + a read-only `material-suggest` endpoint over receipts' `items_json`, all in `dashboard/app.py`. Frontend additions in `dashboard/templates/ahb123.html`: a picker row inside Itemized materials mode (datalist autocomplete merging catalog ★ + receipt 🧾), a body-level "Materials Catalog" manage modal, and a 📌 save-to-catalog action. Project materials still persist via the existing `m4MatRows` → breakdown line-items path (no quote schema change).

**Tech Stack:** Flask + SQLite (`baza_projects.db`), vanilla JS, Jinja template (cached — requires `sudo systemctl restart baza-dashboard`).

**Spec:** `docs/superpowers/specs/2026-06-16-materials-catalog-picker-design.md`

---

### Task 1: Backend — table, seed, CRUD + suggest endpoints (TDD)

**Files:**
- Modify: `dashboard/app.py` (add `_MATERIALS_SEED`, `_ensure_materials_catalog`, call it in `_ensure_estimator_v2` ~14066, add 3 routes after equipment routes ~14349)
- Test: `tests/test_materials_catalog.py` (create)

- [ ] **Step 1: Write failing tests** `tests/test_materials_catalog.py`:

```python
"""Materials catalog picker — CRUD + receipt-suggestion endpoints (Method 4)."""
import importlib, json, os, sqlite3, sys
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
    conn.commit(); conn.close()

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
    hd = conn.execute("SELECT COUNT(*) FROM ahb_materials_catalog WHERE vendor='Home Depot'").fetchone()[0]
    conn.close()
    assert n >= 50
    assert hd >= 40  # the bulk of the seed is Home Depot


def test_list_returns_seeded_rows(client):
    r = client.get("/api/ahb/estimator/materials")
    assert r.status_code == 200
    rows = r.get_json()
    assert isinstance(rows, list) and len(rows) >= 50
    assert all("vendor" in x and "name" in x and "unit_price" in x for x in rows)


def test_create_update_delete(client):
    # create
    r = client.post("/api/ahb/estimator/materials",
                    json={"vendor": "Amazon", "name": "Test Widget", "unit": "each", "unit_price": 9.5})
    assert r.status_code == 200 and r.get_json()["success"] is True
    mid = r.get_json()["id"]
    # update
    r = client.post("/api/ahb/estimator/materials",
                    json={"id": mid, "vendor": "Amazon", "name": "Test Widget 2", "unit_price": 12})
    assert r.get_json()["success"] is True
    rows = client.get("/api/ahb/estimator/materials").get_json()
    assert any(x["id"] == mid and x["name"] == "Test Widget 2" for x in rows)
    # delete (soft)
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
    c.execute("INSERT INTO ahb_receipts (id,vendor,store_name,category,receipt_date,items_json) VALUES (?,?,?,?,?,?)",
              (rid, vendor, vendor, category, date, json.dumps(items)))
    c.commit(); c.close()


def test_suggest_groups_and_picks_latest_price(client, temp_ahb_db):
    _seed_receipt(temp_ahb_db, "r1", "Home Depot",
                  [{"name": "2x4x8 STUD", "price": 3.98}], date="2026-01-01")
    _seed_receipt(temp_ahb_db, "r2", "Home Depot",
                  [{"name": "2x4x8 STUD", "price": 4.25}, {"name": "DECK SCREWS 5LB", "price": 28}], date="2026-05-01")
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


def test_suggest_tolerates_bad_items_json(client, temp_ahb_db):
    c = sqlite3.connect(temp_ahb_db)
    c.execute("INSERT INTO ahb_receipts (id,vendor,store_name,category,receipt_date,items_json) VALUES (?,?,?,?,?,?)",
              ("bad1", "Home Depot", "Home Depot", "Materials", "2026-03-01", "not json"))
    c.execute("INSERT INTO ahb_receipts (id,vendor,store_name,category,receipt_date,items_json) VALUES (?,?,?,?,?,?)",
              ("empty1", "Home Depot", "Home Depot", "Materials", "2026-03-01", ""))
    c.commit(); c.close()
    r = client.get("/api/ahb/estimator/material-suggest?vendor=Home Depot")
    assert r.status_code == 200
    assert isinstance(r.get_json(), list)  # no 500
```

- [ ] **Step 2: Run, expect fail** — `venv/bin/python -m pytest tests/test_materials_catalog.py -x` → AttributeError `_ensure_materials_catalog` / 404 routes.

- [ ] **Step 3: Implement in `dashboard/app.py`.** Add `_MATERIALS_SEED` (≥50 rows, ~45 Home Depot across lumber/drywall/concrete/fasteners/paint/electrical/plumbing/insulation/fixtures + a handful Lowe's/Amazon) and `_ensure_materials_catalog(conn)` next to `_EQUIPMENT_SEED` (~14026). Call `_ensure_materials_catalog(conn)` inside `_ensure_estimator_v2()` right before `conn.commit()` (~14070). Add the 3 routes after the equipment delete route (~14349). Endpoint code:

```python
@app.route('/api/ahb/estimator/materials', methods=['GET', 'POST'])
def api_estimator_materials():
    conn = _ahb_db()
    if request.method == 'GET':
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM ahb_materials_catalog WHERE active=1 ORDER BY vendor, name").fetchall()]
        conn.close()
        return jsonify(rows)
    d = request.get_json() or {}
    name = (d.get('name') or '').strip()
    if not name:
        conn.close()
        return jsonify({'success': False, 'error': 'name required'}), 400
    vals = ((d.get('vendor') or '').strip(), name, (d.get('unit') or 'each').strip(),
            float(d.get('unit_price') or 0), d.get('sku'), d.get('category'), d.get('notes'))
    if d.get('id'):
        conn.execute("""UPDATE ahb_materials_catalog
                        SET vendor=?, name=?, unit=?, unit_price=?, sku=?, category=?, notes=? WHERE id=?""",
                     vals + (int(d['id']),))
        mid = int(d['id'])
    else:
        cur = conn.execute("""INSERT INTO ahb_materials_catalog (vendor,name,unit,unit_price,sku,category,notes)
                              VALUES (?,?,?,?,?,?,?)""", vals)
        mid = cur.lastrowid
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'id': mid})


@app.route('/api/ahb/estimator/materials/<int:mid>', methods=['DELETE'])
def api_estimator_materials_delete(mid):
    conn = _ahb_db()
    conn.execute("UPDATE ahb_materials_catalog SET active=0 WHERE id=?", (mid,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


@app.route('/api/ahb/estimator/material-suggest', methods=['GET'])
def api_estimator_material_suggest():
    vendor = (request.args.get('vendor') or '').strip()
    q = (request.args.get('q') or '').strip().lower()
    conn = _ahb_db()
    try:
        rows = conn.execute("""
            SELECT COALESCE(NULLIF(store_name,''), vendor) AS v, items_json, receipt_date
            FROM ahb_receipts
            WHERE category='Materials' AND items_json IS NOT NULL AND items_json != ''
        """).fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return jsonify([])
    conn.close()
    agg = {}
    for r in rows:
        v = ((r['v'] if not isinstance(r, sqlite3.Row) else r['v']) or '').strip()
        if vendor and v.lower() != vendor.lower():
            continue
        try:
            items = json.loads(r['items_json'] or '[]')
        except (ValueError, TypeError):
            continue
        if not isinstance(items, list):
            continue
        date = r['receipt_date'] or ''
        for it in items:
            if not isinstance(it, dict):
                continue
            nm = str(it.get('name') or '').strip()
            if len(nm) < 3:
                continue
            if q and q not in nm.lower():
                continue
            try:
                price = float(it.get('price') or 0)
            except (ValueError, TypeError):
                price = 0.0
            key = (v.lower(), nm.lower())
            cur = agg.get(key)
            if cur is None:
                agg[key] = {'vendor': v, 'name': nm, 'last_price': price, 'freq': 1, '_date': date}
            else:
                cur['freq'] += 1
                if date >= cur['_date']:
                    cur['_date'] = date
                    cur['last_price'] = price
    out = sorted(agg.values(), key=lambda x: x['freq'], reverse=True)[:25]
    for o in out:
        o.pop('_date', None)
    return jsonify(out)
```

`_ensure_materials_catalog`:

```python
def _ensure_materials_catalog(conn):
    """Local materials catalog for Method 4 (mirrors ahb_equipment_catalog).
    Seeds common Home Depot/Lowe's/Amazon products on first create. Idempotent."""
    conn.execute("""CREATE TABLE IF NOT EXISTS ahb_materials_catalog (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        vendor TEXT,
        name TEXT NOT NULL,
        unit TEXT DEFAULT 'each',
        unit_price REAL DEFAULT 0,
        sku TEXT,
        category TEXT,
        notes TEXT,
        active INTEGER DEFAULT 1
    )""")
    if conn.execute("SELECT COUNT(*) FROM ahb_materials_catalog").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO ahb_materials_catalog (vendor,name,unit,unit_price,category) VALUES (?,?,?,?,?)",
            _MATERIALS_SEED)
```

- [ ] **Step 4: Run, expect pass** — `venv/bin/python -m pytest tests/test_materials_catalog.py -v`
- [ ] **Step 5: Commit** (let claw-auto-git handle, or commit if time-sensitive).

### Task 2: Frontend — picker row, datalist logic, manage modal, init wiring

**Files:**
- Modify: `dashboard/templates/ahb123.html` (picker HTML in `m4-mat-items-row` ~1614; modal after `eqCatalogModal` ~1952; JS globals + functions ~9860–9866 and after `eqcAddNew` ~10297; init call ~9491)

- [ ] **Step 1: Add picker HTML** inside `#m4-mat-items-row`, before `#m4-mat-table` (vendor select, product input+datalist, unit $, qty, +Add; help line with 📌 Save to catalog + 🧱 Manage Catalog buttons).
- [ ] **Step 2: Add body-level `#materialsCatalogModal`** after line 1952 (vendor/name/unit/unit$ table + add-new row), mirroring `eqCatalogModal`.
- [ ] **Step 3: Add JS** — globals `MAT_CATALOG`, `DEFAULT_MAT_VENDORS`, `m4MatPickIndex`, `m4MatSuggestTimer`; functions `loadMaterialsCatalog`, `m4PopulateMaterialVendors`, `m4BuildMatDatalist`, `m4PopulateMaterialPicker`, `m4FetchMatSuggest`, `m4MatPickChanged`, `onMatVendorChange`, `m4AddMaterialFromPicker`, `m4SaveMatPickToCatalog`, and modal `openMaterialsCatalogModal`/`mcRender`/`mcSaveRow`/`mcDeleteRow`/`mcAddNew` (mirror `eqc*`). Add `loadMaterialsCatalog();` beside `loadEquipmentCatalog();` (~9491) and a populate call in `m4Init`.
- [ ] **Step 4: Restart + smoke test** — `sudo systemctl restart baza-dashboard`; `curl -s localhost:8888/api/ahb/estimator/materials | head`; verify HTML elements present via curl grep.
- [ ] **Step 5: Commit.**

### Self-review notes
- Spec coverage: table+seed (Task1), CRUD endpoints (Task1), suggest endpoint (Task1), picker UI (Task2), manage modal (Task2), opt-in save (Task2 `m4SaveMatPickToCatalog`), no quote-schema change (uses existing `m4MatRows`). ✓
- Receipt resilience: bad/empty `items_json` test + try/except. ✓
- Naming consistency: catalog fns `mc*`, picker fns `m4*Material*`, endpoints `/api/ahb/estimator/materials[ -suggest]`. ✓
