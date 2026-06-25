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


# ---- Task 8: _project_total_paid ----

def test_total_paid_sums_across_invoices(app_module):
    conn = app_module._ahb_db()
    conn.execute("INSERT INTO ahb_projects (id,title) VALUES ('p1','x')")
    conn.execute("INSERT INTO ahb_invoices (id,project_id,total,is_primary) VALUES ('i1','p1',20000,1)")
    conn.execute("INSERT INTO ahb_invoices (id,project_id,total) VALUES ('i2','p1',0)")
    conn.execute("INSERT INTO ahb_payments (id,invoice_id,amount) VALUES ('y1','i1',6000)")
    conn.execute("INSERT INTO ahb_payments (id,invoice_id,amount) VALUES ('y2','i2',6000)")
    conn.commit()
    assert app_module._project_total_paid(conn, 'p1') == 12000.0
    conn.close()


# ---- Task 9: _compute_milestone_amount_due ----

def test_milestone_due_exact_payments(app_module):
    ms = [{"label": "Deposit", "pct": 30}, {"label": "Progress", "pct": 30}, {"label": "Final", "pct": 40}]
    f = app_module._compute_milestone_amount_due
    assert f(20000, ms, 0, 0) == 6000
    assert f(20000, ms, 1, 6000) == 6000
    assert f(20000, ms, 2, 12000) == 8000


def test_milestone_due_underpaid_deposit_self_heals(app_module):
    ms = [{"label": "Deposit", "pct": 50}, {"label": "Final", "pct": 50}]
    f = app_module._compute_milestone_amount_due
    assert f(20000, ms, 1, 9000) == 11000


def test_milestone_due_overpaid_clamps_zero(app_module):
    ms = [{"label": "Deposit", "pct": 50}, {"label": "Progress", "pct": 25}, {"label": "Final", "pct": 25}]
    f = app_module._compute_milestone_amount_due
    assert f(20000, ms, 1, 15000) == 0


# ---- Task 10 + 11: next-invoice route + balance-invoice compat ----

@pytest.fixture
def client(app_module):
    conn = app_module._ahb_db()
    conn.execute("INSERT INTO ahb_projects (id,title,status) VALUES ('p1','Smith','Planning')")
    conn.execute("INSERT INTO ahb_invoices (id,project_id,invoice_number,subtotal,total,is_primary,line_items) "
                 "VALUES ('i1','p1','AHB-1',20000,20000,1,'[]')")
    conn.commit(); conn.close()
    return app_module.app.test_client()


def _set_terms(client, preset):
    return client.put("/api/ahb/projects/p1/payment-terms", json={"preset": preset})


def test_next_invoice_requires_terms(client):
    r = client.post("/api/ahb/projects/p1/next-invoice")
    assert r.status_code == 400


def test_next_invoice_issues_second_milestone(client, app_module):
    _set_terms(client, "50_50")
    client.post("/api/ahb/payments", json={"invoice_id": "i1", "amount": 10000})
    r = client.post("/api/ahb/projects/p1/next-invoice")
    assert r.status_code == 200
    body = r.get_json()
    assert body["milestone_index"] == 1
    assert body["milestone_label"] == "Completion"
    assert body["amount_due"] == 10000
    conn = app_module._ahb_db()
    inv = dict(conn.execute("SELECT * FROM ahb_invoices WHERE id=?", (body["id"],)).fetchone())
    conn.close()
    assert inv["parent_invoice_id"] == "i1"
    assert float(inv["subtotal"]) == 20000
    assert inv["is_primary"] == 0


def test_next_invoice_409_when_all_issued(client):
    _set_terms(client, "50_50")
    client.post("/api/ahb/projects/p1/next-invoice")
    r = client.post("/api/ahb/projects/p1/next-invoice")
    assert r.status_code == 409


def test_primary_stamped_as_deposit_on_terms_set(client, app_module):
    _set_terms(client, "30_30_40")
    conn = app_module._ahb_db()
    inv = dict(conn.execute("SELECT * FROM ahb_invoices WHERE id='i1'").fetchone())
    conn.close()
    assert inv["milestone_index"] == 0
    assert inv["milestone_label"] == "Deposit"
    assert inv["amount_due"] == 6000   # 30% of 20000, nothing paid


def test_balance_invoice_still_works_without_terms(client):
    client.post("/api/ahb/payments", json={"invoice_id": "i1", "amount": 5000})
    r = client.post("/api/ahb/projects/p1/balance-invoice")
    assert r.status_code == 200
    assert r.get_json()["balance"] == 15000


# ---- Dollar mode: _compute_milestone_amount_due ----

def test_amount_mode_returns_typed_amount(app_module):
    ms = [{"label": "Deposit", "amount": 5000},
          {"label": "Draw", "amount": 3000},
          {"label": "Balance", "amount": 4000}]
    f = app_module._compute_milestone_amount_due
    assert f(99999, ms, 0, 0, "amount") == 5000
    assert f(99999, ms, 1, 5000, "amount") == 3000
    assert f(99999, ms, 2, 8000, "amount") == 4000   # final is NOT a remainder


def test_amount_mode_clamps_negative_typed_amount(app_module):
    ms = [{"label": "Deposit", "amount": -10}]
    f = app_module._compute_milestone_amount_due
    assert f(99999, ms, 0, 0, "amount") == 0


def test_percent_mode_default_unchanged(app_module):
    ms = [{"label": "Deposit", "pct": 30}, {"label": "Progress", "pct": 30}, {"label": "Final", "pct": 40}]
    f = app_module._compute_milestone_amount_due
    assert f(20000, ms, 2, 12000) == 8000
