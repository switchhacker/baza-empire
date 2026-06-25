"""Status <-> money decoupling + delete cascade (2026-06-25).

Contract enforced here:
  * Project status (Planning / In Progress / Completed) is INFORMATIONAL only.
    Changing status must never mark an invoice Paid, never stamp/clear
    paid_date, and never create a payment.
  * "Paid" revenue (what Uncle Sam counts) comes ONLY from an invoice's
    explicit status == 'Paid'. The ahb_payments ledger does NOT decide
    whether a project is paid.
  * Recording a payment must not move the status bar.
  * Deleting a project purges its invoices + payments so it vanishes from
    every revenue total.
"""
import importlib
import os
import sys

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


def _seed(conn, status="Sent", total=1000, project_status="Planning"):
    conn.execute(
        "INSERT INTO ahb_projects (id,title,status) VALUES ('p1','x',?)",
        (project_status,),
    )
    conn.execute(
        "INSERT INTO ahb_invoices (id,project_id,invoice_number,subtotal,total,is_primary,status,line_items) "
        "VALUES ('i1','p1','INV-1',?,?,1,?,'[]')",
        (total, total, status),
    )
    conn.commit()


# 1 — Completing a project must NOT mark its invoice Paid, even when the ledger covers it.
def test_completing_project_does_not_mark_invoice_paid(app_module):
    conn = app_module._ahb_db()
    _seed(conn, status="Sent", total=1000)
    conn.execute("INSERT INTO ahb_payments (id,invoice_id,amount) VALUES ('y1','i1',1000)")
    conn.commit()
    app_module._ahb_apply_status_sync(conn, "p1", "Completed")
    conn.commit()
    inv = dict(conn.execute("SELECT status FROM ahb_invoices WHERE id='i1'").fetchone())
    proj = dict(conn.execute("SELECT status FROM ahb_projects WHERE id='p1'").fetchone())
    conn.close()
    assert proj["status"] == "Completed"
    assert inv["status"].lower() != "paid"


# 2 — Status changes must not stamp or clear paid_date (no un-paying via the status bar).
def test_status_sync_does_not_touch_paid_state(app_module):
    conn = app_module._ahb_db()
    _seed(conn, status="Paid", total=1000)
    conn.execute("UPDATE ahb_invoices SET paid_date='2026-01-15' WHERE id='i1'")
    conn.commit()
    app_module._ahb_apply_status_sync(conn, "p1", "In Progress")
    conn.commit()
    inv = dict(conn.execute("SELECT status,paid_date FROM ahb_invoices WHERE id='i1'").fetchone())
    conn.close()
    assert inv["status"].lower() == "paid"
    assert inv["paid_date"] == "2026-01-15"


# 3 — Recording a payment must not advance the project status.
def test_recording_payment_does_not_change_status(app_module):
    conn = app_module._ahb_db()
    _seed(conn, status="Sent", total=1000, project_status="Planning")
    conn.close()
    client = app_module.app.test_client()
    r = client.post("/api/ahb/payments", json={"invoice_id": "i1", "amount": 500})
    assert r.status_code == 200
    conn = app_module._ahb_db()
    proj = dict(conn.execute("SELECT status FROM ahb_projects WHERE id='p1'").fetchone())
    conn.close()
    assert proj["status"] == "Planning"


# 4 — Payment summary tracks invoice Paid status, not the ledger.
def test_payment_summary_paid_invoice_no_ledger(app_module):
    conn = app_module._ahb_db()
    _seed(conn, status="Paid", total=1000)
    s = app_module._ahb_project_payment_summary(conn, "p1")
    conn.close()
    assert s["fully_paid"] is True
    assert s["paid"] == 1000
    assert s["owed"] == 0


# 4b — A ledger row does NOT make an unpaid invoice count as paid.
def test_payment_summary_unpaid_invoice_ignores_ledger(app_module):
    conn = app_module._ahb_db()
    _seed(conn, status="Sent", total=1000)
    conn.execute("INSERT INTO ahb_payments (id,invoice_id,amount) VALUES ('y1','i1',1000)")
    conn.commit()
    s = app_module._ahb_project_payment_summary(conn, "p1")
    conn.close()
    assert s["fully_paid"] is False
    assert s["owed"] == 1000


# 5 — Deleting a project purges its invoices + payments.
def test_delete_project_cascades(app_module):
    conn = app_module._ahb_db()
    _seed(conn, status="Paid", total=5000)
    conn.execute("INSERT INTO ahb_payments (id,invoice_id,amount) VALUES ('y1','i1',5000)")
    conn.commit()
    conn.close()
    client = app_module.app.test_client()
    r = client.delete("/api/ahb/projects/p1")
    assert r.status_code == 200
    conn = app_module._ahb_db()
    n_inv = conn.execute("SELECT COUNT(*) FROM ahb_invoices WHERE project_id='p1'").fetchone()[0]
    n_pay = conn.execute("SELECT COUNT(*) FROM ahb_payments WHERE invoice_id='i1'").fetchone()[0]
    conn.close()
    assert n_inv == 0
    assert n_pay == 0


# 6 — Uncle Sam gross counts only Paid invoices, once each (regression guard).
def test_billing_summary_counts_only_paid(app_module):
    conn = app_module._ahb_db()
    conn.execute("INSERT INTO ahb_projects (id,title,status) VALUES ('p1','x','Completed')")
    conn.execute(
        "INSERT INTO ahb_invoices (id,project_id,invoice_number,subtotal,total,is_primary,status,line_items,year) "
        "VALUES ('i1','p1','INV-1',1000,1000,1,'Paid','[]','2026')"
    )
    conn.execute(
        "INSERT INTO ahb_invoices (id,project_id,invoice_number,subtotal,total,status,line_items,year) "
        "VALUES ('i2','p1','INV-2',500,500,'Sent','[]','2026')"
    )
    conn.commit()
    conn.close()
    client = app_module.app.test_client()
    j = client.get("/api/ahb/billing/summary?year=2026").get_json()
    assert j["paid"]["total"] == 1000
    assert j["paid"]["count"] == 1
