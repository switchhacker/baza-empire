"""Tests for AHB123 invoice Terms & Conditions customisation.

Task 1  — constant + settings table seeding
Task 2  — _resolve_invoice_terms precedence
Task 3  — API endpoints + project update persistence
"""
import os
import sys
import sqlite3
import pytest

# Make dashboard/ importable as a flat package from the test dir,
# AND make the parent (agent-framework-v3) importable so that
# `from dashboard.private_inbound import ...` inside app.py resolves.
DASHBOARD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARENT_DIR = os.path.dirname(DASHBOARD_DIR)
for _p in (DASHBOARD_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import app as appmod


# ---------------------------------------------------------------------------
# Task 1 test
# ---------------------------------------------------------------------------

def _db():
    return sqlite3.connect(os.path.join(appmod.DASHBOARD_DIR, "baza_projects.db"))


def _save_terms_default():
    con = _db()
    row = con.execute(
        "SELECT terms_default FROM ahb_invoice_settings WHERE id=1"
    ).fetchone()
    con.close()
    return row[0] if row else None


def _set_terms_default(val):
    con = _db()
    con.execute(
        "INSERT INTO ahb_invoice_settings (id, terms_default) VALUES (1, ?) "
        "ON CONFLICT(id) DO UPDATE SET terms_default=excluded.terms_default",
        (val,),
    )
    con.commit()
    con.close()


def test_invoice_settings_seeded():
    """Seeding logic: a fresh row 1 gets DEFAULT_INVOICE_TERMS.

    Non-destructive: save and restore the live company default around the test
    so the production setting is never left mutated by the suite.
    """
    saved = _save_terms_default()
    try:
        con = _db()
        con.execute("DELETE FROM ahb_invoice_settings WHERE id=1")
        con.commit()
        con.close()
        appmod._ensure_invoice_settings()
        seeded = _save_terms_default()
        assert seeded == appmod.DEFAULT_INVOICE_TERMS
        assert "ALL HOME BUILDING" in seeded.upper()
    finally:
        _set_terms_default(saved if saved is not None else appmod.DEFAULT_INVOICE_TERMS)


# ---------------------------------------------------------------------------
# Task 2 test
# ---------------------------------------------------------------------------

def test_resolve_invoice_terms_precedence():
    f = appmod._resolve_invoice_terms
    assert f({"terms_conditions": "PROJECT TERMS"}, "COMPANY") == "PROJECT TERMS"
    assert f({"terms_conditions": "   "}, "COMPANY") == "COMPANY"
    assert f({}, "") == appmod.DEFAULT_INVOICE_TERMS


# ---------------------------------------------------------------------------
# Task 3 fixtures + tests
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    appmod.app.config["TESTING"] = True
    appmod.app.config["SECRET_KEY"] = "test-secret"
    with appmod.app.test_client() as c:
        yield c


@pytest.fixture
def a_project_id(client):
    """Create a minimal project and return its id."""
    res = client.post(
        "/api/ahb/projects",
        json={"title": "Test Project Terms", "status": "Planning"},
        content_type="application/json",
    )
    data = res.get_json()
    assert data.get("success"), f"Could not create project: {data}"
    yield data["id"]
    # Cleanup (best-effort)
    try:
        import sqlite3 as _sq
        con = _sq.connect(os.path.join(appmod.DASHBOARD_DIR, "baza_projects.db"))
        con.execute("DELETE FROM ahb_projects WHERE id = ?", (data["id"],))
        con.execute("DELETE FROM ahb_invoices WHERE project_id = ?", (data["id"],))
        con.commit()
        con.close()
    except Exception:
        pass


def test_invoice_settings_get_put(client):
    """PUT then GET round-trips. Non-destructive: restore the original company
    default afterwards so the suite never leaves production mutated."""
    saved = _save_terms_default()
    try:
        client.put(
            "/api/ahb/invoice-settings",
            json={"terms_default": "NEW CO TERMS"},
            content_type="application/json",
        )
        got = client.get("/api/ahb/invoice-settings").get_json()
        assert got["terms_default"] == "NEW CO TERMS"
    finally:
        _set_terms_default(saved if saved is not None else appmod.DEFAULT_INVOICE_TERMS)


def test_project_update_persists_terms(client, a_project_id):
    client.put(
        f"/api/ahb/projects/{a_project_id}",
        json={"terms_conditions": "PROJ TERMS"},
        content_type="application/json",
    )
    proj = client.get("/api/ahb/projects").get_json()
    # GET /api/ahb/projects returns a list directly
    row = [p for p in proj if p["id"] == a_project_id][0]
    assert row["terms_conditions"] == "PROJ TERMS"
