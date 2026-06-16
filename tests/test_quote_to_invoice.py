import importlib, os, sys
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)


@pytest.fixture
def app_module(tmp_path, monkeypatch):
    monkeypatch.setenv("BAZA_PROJECTS_DB", str(tmp_path / "test.db"))
    sys.modules.pop("dashboard.app", None)
    mod = importlib.import_module("dashboard.app")
    mod.app.config["TESTING"] = True
    return mod


# ---- Task 5: _invoice_line_items_from_quote ----

def test_structured_breakdown_line_items(app_module):
    quote = {"total": 20000, "description": "ignore me",
             "breakdown": {"line_items": [
                 {"description": "Framing", "total": 8000},
                 {"description": "Cabinets", "total": 12000}]}}
    items = app_module._invoice_line_items_from_quote(quote)
    assert [i["description"] for i in items] == ["Framing", "Cabinets"]
    assert items[0]["total"] == 8000 and items[0]["include_in_total"] is True


def test_breakdown_as_json_string(app_module):
    import json
    quote = {"total": 5000,
             "breakdown": json.dumps({"line_items": [{"description": "Demo", "total": 5000}]})}
    items = app_module._invoice_line_items_from_quote(quote)
    assert [i["description"] for i in items] == ["Demo"]


def test_falls_back_to_description(app_module):
    quote = {"total": 5000, "description": "Demo\nHaul away", "breakdown": {}}
    items = app_module._invoice_line_items_from_quote(quote)
    assert [i["description"] for i in items] == ["Demo", "Haul away"]


# ---- Task 6: quote->invoice create-or-replace ----

@pytest.fixture
def client(app_module):
    conn = app_module._ahb_db()
    conn.execute("INSERT INTO ahb_projects (id,title,status) VALUES ('p1','Smith','Planning')")
    conn.commit(); conn.close()
    return app_module.app.test_client()


def _make_quote(client, **extra):
    body = {"total": 20000, "description": "Framing\nCabinets",
            "breakdown": {"line_items": [{"description": "Framing", "total": 8000},
                                          {"description": "Cabinets", "total": 12000}]},
            "make_active": True}
    body.update(extra)
    return client.post("/api/ahb/projects/p1/quotes", json=body)


def test_active_quote_creates_primary_when_none(client, app_module):
    r = _make_quote(client)
    assert r.status_code == 200
    conn = app_module._ahb_db()
    invs = [dict(x) for x in conn.execute("SELECT * FROM ahb_invoices WHERE project_id='p1'").fetchall()]
    conn.close()
    assert len(invs) == 1
    items = app_module._parse_line_items(invs[0]["line_items"])
    assert [i["description"] for i in items] == ["Framing", "Cabinets"]
    assert invs[0]["is_primary"] == 1
    assert float(invs[0]["subtotal"]) == 20000


def test_existing_primary_requires_decision(client):
    _make_quote(client)
    r = _make_quote(client, on_existing=None)
    assert r.status_code == 409
    assert "on_existing" in r.get_json()["error"]


def test_existing_primary_new_creates_second(client, app_module):
    _make_quote(client)
    r = _make_quote(client, on_existing="new")
    assert r.status_code == 200
    conn = app_module._ahb_db()
    n = conn.execute("SELECT count(*) c FROM ahb_invoices WHERE project_id='p1'").fetchone()["c"]
    conn.close()
    assert n == 2


def test_existing_primary_replace_keeps_one(client, app_module):
    _make_quote(client)
    r = _make_quote(client, total=25000,
                    breakdown={"line_items": [{"description": "Revised scope", "total": 25000}]},
                    on_existing="replace")
    assert r.status_code == 200
    conn = app_module._ahb_db()
    invs = [dict(x) for x in conn.execute("SELECT * FROM ahb_invoices WHERE project_id='p1'").fetchall()]
    conn.close()
    assert len(invs) == 1
    assert float(invs[0]["subtotal"]) == 25000
