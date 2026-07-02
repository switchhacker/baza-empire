import os, sys
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))
from flask import Flask
import network_routes


def make_app(tmp_path, monkeypatch):
    monkeypatch.setattr(network_routes.network_db, "DEFAULT_DB", str(tmp_path / "n.db"))
    app = Flask("t", template_folder=os.path.join(REPO_ROOT, "dashboard", "templates"),
                static_folder=os.path.join(REPO_ROOT, "dashboard", "static"))
    network_routes.init_network()
    app.register_blueprint(network_routes.network_bp)
    return app.test_client()


def test_status_and_action(tmp_path, monkeypatch):
    c = make_app(tmp_path, monkeypatch)
    monkeypatch.setattr(network_routes.network_probe, "status", lambda: {"edges": [], "ts": "x"})
    assert c.get("/api/network/status").status_code == 200
    r = c.post("/api/network/action", json={"action": "definitely_not_real", "params": {}})
    assert r.status_code == 400
    monkeypatch.setattr(network_routes.network_ops, "run_action",
                        lambda k, p, db_path=None: {"ok": True, "rc": 0, "out": "", "err": "", "action": k})
    assert c.post("/api/network/action", json={"action": "svc", "params": {}}).get_json()["ok"]
    assert c.get("/api/network/audit").status_code == 200


def test_api_action_malformed_json_array(tmp_path, monkeypatch):
    """POST /api/network/action with json=[1] coerces to {} and rejects missing action."""
    c = make_app(tmp_path, monkeypatch)
    monkeypatch.setattr(network_routes.network_ops, "run_action",
                        lambda k, p, db_path=None: {"error": f"unknown action '{k}'"} if not k else {"ok": True, "rc": 0, "out": "", "err": "", "action": k})
    # With array input, body becomes {}, action="" → run_action returns error → ValueError → 400
    def mock_run_action(action, params, db_path=None):
        if not action or action == "definitely_not_real":
            raise ValueError(f"unknown action '{action}'")
        return {"ok": True, "rc": 0, "out": "", "err": "", "action": action}
    monkeypatch.setattr(network_routes.network_ops, "run_action", mock_run_action)
    r = c.post("/api/network/action", json=[1])
    assert r.status_code == 400
    assert "error" in r.get_json()


def test_api_action_malformed_params_not_dict(tmp_path, monkeypatch):
    """POST /api/network/action with params as string coerces to {} and processes normally."""
    c = make_app(tmp_path, monkeypatch)
    def mock_run_action(action, params, db_path=None):
        if not action or action == "definitely_not_real":
            raise ValueError(f"unknown action '{action}'")
        return {"ok": True, "rc": 0, "out": "", "err": "", "action": action}
    monkeypatch.setattr(network_routes.network_ops, "run_action", mock_run_action)
    # params coerced from string to {}, action="svc" is valid → 200
    r = c.post("/api/network/action", json={"action": "svc", "params": "notadict"})
    assert r.status_code == 200
    assert r.get_json()["ok"]


def test_api_audit_limit_non_integer(tmp_path, monkeypatch):
    """GET /api/network/audit?limit=abc should return 200 with default limit."""
    c = make_app(tmp_path, monkeypatch)
    r = c.get("/api/network/audit?limit=abc")
    assert r.status_code == 200
    assert "rows" in r.get_json()


def test_api_audit_limit_negative(tmp_path, monkeypatch):
    """GET /api/network/audit?limit=-5 should clamp to 1 and return 200."""
    c = make_app(tmp_path, monkeypatch)
    r = c.get("/api/network/audit?limit=-5")
    assert r.status_code == 200
    assert "rows" in r.get_json()
