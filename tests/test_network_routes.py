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


# ── Task 13: facts CRUD + seed ─────────────────────────────────────────────

def test_facts_seed_on_init(tmp_path, monkeypatch):
    """init_network() seeds the 4 router manual facts when manual_facts is empty."""
    c = make_app(tmp_path, monkeypatch)
    r = c.get("/api/network/registry")
    assert r.status_code == 200
    data = r.get_json()
    rows = data.get("rows", [])
    router_rows = [r for r in rows if r["group"] == "router"]
    keys = {r["key"] for r in router_rows}
    assert "router.model" in keys
    assert "router.admin" in keys
    assert "router.reservation" in keys
    assert "router.port_forward" in keys
    # Check seeded values
    model_row = next(r for r in router_rows if r["key"] == "router.model")
    assert model_row["value"] == "Fios G3100"


def test_facts_seed_not_duplicated(tmp_path, monkeypatch):
    """Calling init_network() twice does NOT duplicate seed rows."""
    monkeypatch.setattr(network_routes.network_db, "DEFAULT_DB", str(tmp_path / "n.db"))
    app = __import__('flask', fromlist=['Flask']).Flask(
        "t",
        template_folder=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dashboard", "templates"),
        static_folder=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dashboard", "static"),
    )
    network_routes.init_network()
    network_routes.init_network()  # second call — must be idempotent
    app.register_blueprint(network_routes.network_bp)
    c = app.test_client()
    r = c.get("/api/network/registry")
    assert r.status_code == 200
    rows = r.get_json()["rows"]
    router_rows = [r for r in rows if r["group"] == "router"]
    model_rows = [r for r in router_rows if r["key"] == "router.model"]
    assert len(model_rows) == 1


def test_post_fact(tmp_path, monkeypatch):
    """POST /api/network/facts sets a new key."""
    c = make_app(tmp_path, monkeypatch)
    r = c.post("/api/network/facts", json={"key": "router.wan", "value": "96.227.96.20", "note": "current WAN"})
    assert r.status_code == 200
    j = r.get_json()
    assert j.get("ok") is True

    # Verify it's visible in registry
    rr = c.get("/api/network/registry")
    rows = rr.get_json()["rows"]
    router_rows = [x for x in rows if x["group"] == "router"]
    keys = {x["key"] for x in router_rows}
    assert "router.wan" in keys


def test_post_fact_update(tmp_path, monkeypatch):
    """POST /api/network/facts updates an existing key."""
    c = make_app(tmp_path, monkeypatch)
    c.post("/api/network/facts", json={"key": "router.model", "value": "Fios G3100"})
    r = c.post("/api/network/facts", json={"key": "router.model", "value": "Fios G3200", "note": "upgraded"})
    assert r.status_code == 200
    rr = c.get("/api/network/registry")
    rows = rr.get_json()["rows"]
    model_row = next((x for x in rows if x["key"] == "router.model"), None)
    assert model_row is not None
    assert model_row["value"] == "Fios G3200"


def test_post_fact_missing_key(tmp_path, monkeypatch):
    """POST /api/network/facts with missing key returns 400."""
    c = make_app(tmp_path, monkeypatch)
    r = c.post("/api/network/facts", json={"value": "something"})
    assert r.status_code == 400


def test_post_fact_missing_value(tmp_path, monkeypatch):
    """POST /api/network/facts with missing value returns 400."""
    c = make_app(tmp_path, monkeypatch)
    r = c.post("/api/network/facts", json={"key": "router.model"})
    assert r.status_code == 400


def test_delete_fact(tmp_path, monkeypatch):
    """DELETE /api/network/facts/<key> removes the row."""
    c = make_app(tmp_path, monkeypatch)
    # First add a custom fact
    c.post("/api/network/facts", json={"key": "router.test", "value": "temp"})
    r = c.delete("/api/network/facts/router.test")
    assert r.status_code == 200
    assert r.get_json().get("ok") is True
    # Confirm gone
    rr = c.get("/api/network/registry")
    rows = rr.get_json()["rows"]
    router_rows = [x for x in rows if x["group"] == "router"]
    assert not any(x["key"] == "router.test" for x in router_rows)


def test_delete_fact_nonexistent(tmp_path, monkeypatch):
    """DELETE /api/network/facts/<key> on a nonexistent key returns 200 (idempotent)."""
    c = make_app(tmp_path, monkeypatch)
    r = c.delete("/api/network/facts/no.such.key")
    assert r.status_code == 200


def test_registry_route_shape(tmp_path, monkeypatch):
    """GET /api/network/registry returns {rows: [...]} with group/key/value/source/edit on each."""
    c = make_app(tmp_path, monkeypatch)
    monkeypatch.setattr(network_routes.network_probe, "status", lambda: {
        "caddy": {"active": False, "sites": [], "valid": False, "validate_err": None, "backups": []},
        "cloudflared": {"installed": False, "config_exists": False, "unit_state": "unknown", "tunnels": ""},
        "tailscale": {"self": {}, "peers": [], "serves": []},
        "services": {},
    })
    r = c.get("/api/network/registry")
    assert r.status_code == 200
    data = r.get_json()
    assert "rows" in data
    for row in data["rows"]:
        for field in ("group", "key", "value", "source", "edit"):
            assert field in row, f"row missing {field!r}: {row}"
