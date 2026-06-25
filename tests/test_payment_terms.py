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


def test_custom_must_sum_to_100(app_module):
    with pytest.raises(ValueError):
        app_module._resolve_payment_terms(
            "custom", [{"label": "A", "pct": 40}, {"label": "B", "pct": 40}])


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


def test_put_custom_bad_sum_rejected(client):
    r = client.put("/api/ahb/projects/p1/payment-terms",
                   json={"preset": "custom",
                         "milestones": [{"label": "A", "pct": 60},
                                        {"label": "B", "pct": 50}]})
    assert r.status_code == 400
    assert "100" in r.get_json()["error"]


# ---- Dollar mode: _resolve_payment_terms ----

def test_amount_mode_resolves_and_keeps_amounts(app_module):
    t = app_module._resolve_payment_terms(
        "custom",
        [{"label": "Deposit", "amount": 5000},
         {"label": "Draw", "amount": 3000},
         {"label": "Balance upon completion", "amount": 4000}],
        "amount")
    assert t["mode"] == "amount"
    assert t["preset"] == "custom"
    assert [m["amount"] for m in t["milestones"]] == [5000, 3000, 4000]
    assert [m["label"] for m in t["milestones"]] == ["Deposit", "Draw", "Balance upon completion"]


def test_amount_mode_skips_sum_check(app_module):
    t = app_module._resolve_payment_terms(
        "custom", [{"label": "A", "amount": 9999}], "amount")
    assert t["milestones"][0]["amount"] == 9999


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
