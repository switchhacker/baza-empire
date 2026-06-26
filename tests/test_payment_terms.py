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


def _cols(app_module, table):
    conn = app_module._ahb_db()
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    finally:
        conn.close()


def test_new_columns_exist(app_module):
    proj = _cols(app_module, "ahb_projects")
    inv = _cols(app_module, "ahb_invoices")
    assert "payment_terms" in proj
    assert {"milestone_label", "milestone_index", "amount_due", "terms_snapshot"} <= inv


# ---- Task 2: _resolve_payment_terms ----

def test_preset_50_50_resolves(app_module):
    t = app_module._resolve_payment_terms("50_50", None)
    assert t["preset"] == "50_50"
    assert [m["label"] for m in t["milestones"]] == ["Deposit", "Completion"]
    assert sum(m["pct"] for m in t["milestones"]) == 100


def test_preset_30_30_40_resolves(app_module):
    t = app_module._resolve_payment_terms("30_30_40", None)
    assert [m["pct"] for m in t["milestones"]] == [30, 30, 40]


def test_net_30_sets_net_days(app_module):
    t = app_module._resolve_payment_terms("net_30", None)
    assert t["net_days"] == 30
    assert t["milestones"][0]["pct"] == 100


def test_custom_no_longer_requires_sum_100(app_module):
    # The auto-remainder final milestone makes the old sum-to-100 rule obsolete:
    # an "uneven" percent schedule now resolves fine and reconciles at billing time.
    t = app_module._resolve_payment_terms(
        "custom", [{"label": "A", "unit": "percent", "pct": 40},
                   {"label": "B", "unit": "percent", "pct": 40}])
    assert len(t["milestones"]) == 2
    assert [m["unit"] for m in t["milestones"]] == ["percent", "percent"]


def test_custom_requires_labels(app_module):
    with pytest.raises(ValueError):
        app_module._resolve_payment_terms("custom", [{"label": "", "pct": 100}])


def test_custom_valid(app_module):
    t = app_module._resolve_payment_terms(
        "custom", [{"label": "Deposit", "pct": 30},
                   {"label": "Rough-in", "pct": 30},
                   {"label": "Completion", "pct": 40}])
    assert t["preset"] == "custom"
    assert len(t["milestones"]) == 3


# ---- Task 3: payment-terms routes ----

@pytest.fixture
def client(app_module):
    conn = app_module._ahb_db()
    conn.execute("INSERT INTO ahb_projects (id, title, status) VALUES ('p1','Smith kitchen','Planning')")
    conn.commit(); conn.close()
    return app_module.app.test_client()


def test_put_and_get_terms(client):
    r = client.put("/api/ahb/projects/p1/payment-terms", json={"preset": "30_30_40"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["success"] is True
    assert [m["pct"] for m in body["terms"]["milestones"]] == [30, 30, 40]

    g = client.get("/api/ahb/projects/p1/payment-terms")
    assert g.get_json()["terms"]["preset"] == "30_30_40"


def test_deposit_pct_dollar_first_uses_contract(app_module):
    # a $5,000 deposit on a $20,000 contract must report 25%, not the 50% default
    terms = {"milestones": [{"label": "Deposit", "unit": "amount", "amount": 5000},
                            {"label": "Balance", "unit": "percent", "pct": 0}]}
    assert app_module._contract_deposit_pct(terms, 20000) == 25.0
    assert app_module._contract_deposit_pct(terms, None) == 50.0   # no contract -> default


def test_deposit_pct_percent_first_and_legacy(app_module):
    pct_terms = {"milestones": [{"label": "Deposit", "unit": "percent", "pct": 30}]}
    assert app_module._contract_deposit_pct(pct_terms, 20000) == 30
    # legacy {label,pct} row (no unit) still resolves to its pct
    assert app_module._contract_deposit_pct({"milestones": [{"label": "D", "pct": 40}]}, 20000) == 40


def test_put_malformed_milestones_returns_400(client):
    for bad in ["notalist", 123, {"x": 1}, ["foo"]]:
        r = client.put("/api/ahb/projects/p1/payment-terms",
                       json={"preset": "custom", "milestones": bad})
        assert r.status_code == 400, bad


def test_put_custom_uneven_sum_accepted(client):
    # uneven percents are accepted now (auto-remainder reconciles); no 400
    r = client.put("/api/ahb/projects/p1/payment-terms",
                   json={"preset": "custom",
                         "milestones": [{"label": "A", "unit": "percent", "pct": 60},
                                        {"label": "B", "unit": "percent", "pct": 50}]})
    assert r.status_code == 200
    assert r.get_json()["success"] is True


# ---- Mixed percent/dollar milestones: _resolve_payment_terms ----

def test_mixed_units_resolve_and_keep_their_unit(app_module):
    t = app_module._resolve_payment_terms(
        "custom",
        [{"label": "Deposit", "unit": "amount", "amount": 5000},
         {"label": "Progress", "unit": "percent", "pct": 25},
         {"label": "Balance upon completion", "unit": "percent", "pct": 0}])
    assert t["preset"] == "custom"
    assert [m["unit"] for m in t["milestones"]] == ["amount", "percent", "percent"]
    assert t["milestones"][0]["amount"] == 5000
    assert t["milestones"][1]["pct"] == 25


def test_unit_inferred_from_shape_for_legacy_rows(app_module):
    # a row with an amount but no unit/pct is read as a dollar row (back-compat)
    t = app_module._resolve_payment_terms(
        "custom", [{"label": "Deposit", "amount": 5000},
                   {"label": "Balance", "pct": 0}])
    assert t["milestones"][0]["unit"] == "amount"
    assert t["milestones"][0]["amount"] == 5000
    assert t["milestones"][1]["unit"] == "percent"


def test_amount_milestone_rejects_negative(app_module):
    with pytest.raises(ValueError):
        app_module._resolve_payment_terms(
            "custom", [{"label": "A", "unit": "amount", "amount": -5}])


def test_amount_mode_requires_label(app_module):
    with pytest.raises(ValueError):
        app_module._resolve_payment_terms("custom", [{"label": "", "amount": 100}], "amount")


def test_amount_mode_rejects_negative(app_module):
    with pytest.raises(ValueError):
        app_module._resolve_payment_terms("custom", [{"label": "A", "amount": -5}], "amount")


def test_amount_mode_rejects_non_numeric(app_module):
    with pytest.raises(ValueError):
        app_module._resolve_payment_terms("custom", [{"label": "A", "amount": "x"}], "amount")


def test_percent_mode_default_when_mode_absent(app_module):
    t = app_module._resolve_payment_terms("50_50", None)
    assert t["mode"] == "percent"
    assert [m["pct"] for m in t["milestones"]] == [50, 50]
