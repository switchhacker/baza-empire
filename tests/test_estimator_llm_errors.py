"""Estimator LLM-error handling.

Regression for the "Expecting value: line 1 column 2 (char 1)" bug Serge hit
running 🔮 Specter Research (estimator method 2):

  _ollama_text() returns the string "[LLM error: ...]" on any Ollama failure
  (e.g. the qwen3.6:27b call exceeding the 120s timeout). method2/method3 then
  blindly json.loads() that string. json.loads("[LLM error: ...]") fails at the
  'L' after the '[' → "Expecting value: line 1 column 2 (char 1)", masking the
  real timeout error. The other _ollama_text callers in app.py already guard
  with `.startswith("[LLM error")`; the estimator methods must do the same.
"""
import os
import sys
import importlib

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)


@pytest.fixture
def app_module(tmp_path, monkeypatch):
    monkeypatch.setenv("BAZA_PROJECTS_DB", str(tmp_path / "test.db"))
    if "dashboard.app" in sys.modules:
        del sys.modules["dashboard.app"]
    mod = importlib.import_module("dashboard.app")
    mod.app.config["TESTING"] = True
    return mod


@pytest.fixture
def client(app_module):
    return app_module.app.test_client()


def _payload():
    return {"description": "Kitchen remodel: cabinets, counters, flooring",
            "scope": "Kitchen remodel", "address": "Bensalem PA", "sqft": 200}


@pytest.mark.parametrize("route", [
    "/api/ahb/estimator/method2",
    "/api/ahb/estimator/method3",
])
def test_llm_error_surfaces_real_message_not_json_decode(client, app_module, monkeypatch, route):
    """When the Ollama call fails, the route must surface the real error,
    not the cryptic JSON-decode message that masks it."""
    monkeypatch.setattr(app_module, "_ollama_text",
                        lambda *a, **k: "[LLM error: HTTP Error: timed out]")
    r = client.post(route, json=_payload())
    assert r.status_code >= 500
    body = r.get_json()
    assert body["success"] is False
    # The mask must be gone...
    assert "Expecting value" not in body["error"]
    # ...and the real cause must reach the user.
    assert "timed out" in body["error"] or "LLM" in body["error"]


def test_method2_parses_valid_json(client, app_module, monkeypatch):
    """A normal valid-JSON response from the model still parses fine."""
    monkeypatch.setattr(app_module, "_ollama_text",
                        lambda *a, **k: '{"labor_cost": 8500, "total_estimate": 25000}')
    r = client.post("/api/ahb/estimator/method2", json=_payload())
    assert r.status_code == 200
    body = r.get_json()
    assert body["success"] is True
    assert body["specter_analysis"]["total_estimate"] == 25000
