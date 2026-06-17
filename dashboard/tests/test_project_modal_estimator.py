"""Tests for bringing Method 5 (unit-cost DB) and the itemized materials
picker into the ahb123 project-detail modal estimator.

Mirrors dashboard/tests/test_mobile_pwa.py: import the real app.py and use its
Flask test client (the shared conftest `app` fixture only wires the email
blueprint, so it can't see these routes).
"""
import os
import sys

import pytest

DASHBOARD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARENT_DIR = os.path.dirname(DASHBOARD_DIR)
for _p in (DASHBOARD_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import app as appmod


@pytest.fixture
def client():
    with appmod.app.test_client() as c:
        yield c


# ── Method 5 in the project modal ──────────────────────────────────────────
def test_method5_pane_present_in_project_modal(client):
    html = client.get("/ahb123").get_data(as_text=True)
    assert "pd-est-pane-5" in html
    assert 'onclick="pdRunMethod(5)"' in html
    assert 'data-m="5"' in html
    assert "repeat(5,1fr)" in html       # method-picker grid widened to 5
    assert "pd-m5-scope" in html         # modal has its own cost-book scope picker


def test_method5_endpoint_modal_payload(client):
    # Robust against seed contents: discover an existing cost-book scope first.
    book = client.get("/api/ahb/estimator/costbook").get_json()
    assert isinstance(book, list) and book, "cost book should be seeded"
    scope = book[0]["scope"]
    res = client.post(
        "/api/ahb/estimator/method5",
        json={"scope": scope, "qty": 200, "tier": "mid", "multiplier": 1.0},
    )
    assert res.status_code == 200
    d = res.get_json()
    assert d["success"] is True
    assert {"low", "mid", "high"}.issubset(d["totals"].keys())
    assert d["selected_total"] == d["totals"]["mid"]


# ── Itemized materials picker in the project modal ──────────────────────────
def test_itemized_materials_picker_present_in_project_modal(client):
    html = client.get("/ahb123").get_data(as_text=True)
    assert "pdM4MatMode" in html                       # Lump/Itemized tab handler
    assert "pd-m4-mat-pick" in html                    # type-ahead product input
    assert "pd-m4-mat-tbody" in html                   # line-items table body
    assert "pdM4AddMaterialFromPicker" in html         # + Add handler
    assert 'data-pdmat="items"' in html                # Itemized tab button


def test_material_suggest_endpoint(client):
    res = client.get("/api/ahb/estimator/material-suggest?vendor=&q=")
    assert res.status_code == 200
    assert isinstance(res.get_json(), list)


def test_materials_catalog_list(client):
    res = client.get("/api/ahb/estimator/materials")
    assert res.status_code == 200
    assert isinstance(res.get_json(), list)
