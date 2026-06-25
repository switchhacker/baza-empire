import importlib, json, os, sys
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)


@pytest.fixture
def app_module(tmp_path, monkeypatch):
    monkeypatch.setenv("BAZA_PROJECTS_DB", str(tmp_path / "test.db"))
    sys.modules.pop("dashboard.app", None)
    mod = importlib.import_module("dashboard.app")
    return mod


def test_non_term_invoice_no_block(app_module):
    assert app_module._payment_schedule_block({"milestone_index": -1, "amount_due": None}) == ""


def test_term_invoice_renders_schedule(app_module):
    terms = {"preset": "50_50", "milestones": [{"label": "Deposit", "pct": 50},
                                               {"label": "Completion", "pct": 50}]}
    inv = {"milestone_index": 1, "amount_due": 10000, "total": 20000, "status": "draft",
           "terms_snapshot": json.dumps(terms)}
    html = app_module._payment_schedule_block(inv)
    assert "PAYMENT SCHEDULE" in html
    assert "Deposit" in html and "Completion" in html
    assert "AMOUNT DUE NOW" in html
    assert "10,000" in html


def test_amount_mode_renders_dollar_schedule(app_module):
    terms = {"preset": "custom", "mode": "amount",
             "milestones": [{"label": "Deposit", "amount": 5000},
                            {"label": "Draw", "amount": 3000},
                            {"label": "Balance upon completion", "amount": 4000}]}
    inv = {"milestone_index": 1, "amount_due": 3000, "total": 20000, "status": "draft",
           "terms_snapshot": json.dumps(terms)}
    html = app_module._payment_schedule_block(inv)
    assert "PAYMENT SCHEDULE" in html
    assert "Deposit" in html and "Draw" in html and "Balance upon completion" in html
    assert "5,000" in html and "3,000" in html and "4,000" in html
    assert "%" not in html            # no percent markers in dollar mode
    assert "AMOUNT DUE NOW" in html


def test_percent_snapshot_still_renders_percent(app_module):
    terms = {"preset": "50_50", "milestones": [{"label": "Deposit", "pct": 50},
                                               {"label": "Completion", "pct": 50}]}
    inv = {"milestone_index": 1, "amount_due": 10000, "total": 20000, "status": "draft",
           "terms_snapshot": json.dumps(terms)}
    html = app_module._payment_schedule_block(inv)
    assert "(50%)" in html or "50%" in html
    assert "10,000" in html
