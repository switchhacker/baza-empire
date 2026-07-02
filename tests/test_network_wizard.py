"""Tests for dashboard/network_wizard.py — Task 10 (Cloudflare migration wizard).

detect() must be PURE (inject status + wizard_db dicts) so no shelling out is
needed to test the phase-resolution logic. Run-action fns and verify helpers
monkeypatch network_probe._run / builtins.open.
"""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))

import pytest
import network_wizard
import network_ops
import network_probe


# ─── PHASES shape ─────────────────────────────────────────────────────────────

def test_phases_shape():
    assert len(network_wizard.PHASES) == 9
    ids = [p["id"] for p in network_wizard.PHASES]
    assert ids == [f"phase{i}" for i in range(9)]
    for p in network_wizard.PHASES:
        assert p["who"] in ("claude", "serge")
        assert isinstance(p["title"], str) and p["title"]
        assert isinstance(p["instructions"], str) and p["instructions"]
        assert "run" in p and "verify" in p
    # Phase 2 warnings must be present (grey-cloud / NS / MX-DKIM)
    p2 = next(p for p in network_wizard.PHASES if p["id"] == "phase2")
    low = p2["instructions"].lower()
    assert "grey cloud" in low or "grey-cloud" in low or "dns only" in low
    assert "nova" in low
    assert "mx" in low and ("dkim" in low or "spf" in low)


# ─── detect() purity: early-state fixture ─────────────────────────────────────

def _status(*, installed=True, config=False, unit="inactive",
            ns_actual=None, baza_ok=False):
    """Build a minimal status() dict for detect()."""
    ns_actual = ns_actual if ns_actual is not None else []
    return {
        "cloudflared": {
            "installed": installed,
            "version": "cloudflared 2026.6.1" if installed else None,
            "config_exists": config,
            "unit_state": unit,
            "tunnels": "",
        },
        "dns": [
            {"name": "ahb123.com", "rtype": "NS", "expected": None,
             "actual": ns_actual, "ok": bool(ns_actual)},
        ],
        "reach": [
            {"url": "https://baza.ahb123.com", "status": 200 if baza_ok else None,
             "ok": baza_ok},
        ],
    }


def test_detect_pure_early():
    """installed + no config + google NS + empty db →
    phase0 done, phase3 todo, phase5 todo."""
    status = _status(installed=True, config=False, unit="inactive",
                     ns_actual=["ns-cloud-e1.googledomains.com",
                                "ns-cloud-e2.googledomains.com"])
    out = network_wizard.detect(status, {})
    by = {p["id"]: p for p in out}
    assert by["phase0"]["state"] == "done"     # cloudflared installed
    assert by["phase3"]["state"] == "todo"     # manual, no db
    assert by["phase4"]["state"] == "todo"     # google NS, not cloudflare
    assert by["phase5"]["state"] == "todo"     # no config
    assert by["phase6"]["state"] == "todo"     # unit inactive
    assert by["phase8"]["state"] == "todo"     # baza not reachable


def test_detect_pure_active():
    """cloudflare NS + config + active unit → phases 3-6 done."""
    status = _status(installed=True, config=True, unit="active",
                     ns_actual=["tia.ns.cloudflare.com", "kip.ns.cloudflare.com"],
                     baza_ok=True)
    # phase3 (manual NS swap) is implied-done by cloudflare NS evidence
    out = network_wizard.detect(status, {})
    by = {p["id"]: p for p in out}
    assert by["phase0"]["state"] == "done"
    assert by["phase4"]["state"] in ("done", "verified")   # cloudflare NS
    assert by["phase5"]["state"] == "done"                 # config exists
    assert by["phase6"]["state"] == "done"                 # unit active
    assert by["phase8"]["state"] in ("done", "verified")   # baza reachable
    # phase3 nameserver swap must be resolved done given active cloudflare NS
    assert by["phase3"]["state"] == "done"


def test_detect_pure_no_sideeffects(monkeypatch):
    """detect() must not shell out — poison _run to prove purity."""
    def _boom(*a, **k):
        raise AssertionError("detect() shelled out — not pure")
    monkeypatch.setattr(network_probe, "_run", _boom)
    status = _status()
    network_wizard.detect(status, {})  # must not raise


def test_detect_manual_from_db():
    """Manual phase (phase1) with no evidence takes state from wizard_db."""
    status = _status()
    out = network_wizard.detect(status, {"phase1": {"state": "done", "note": ""}})
    by = {p["id"]: p for p in out}
    assert by["phase1"]["state"] == "done"


# ─── verify_ns parse ──────────────────────────────────────────────────────────

def test_verify_ns_cloudflare_ok(monkeypatch):
    monkeypatch.setattr(network_probe, "_run",
                        lambda cmd, timeout=8: (0, "tia.ns.cloudflare.com.\nkip.ns.cloudflare.com.\n", ""))
    r = network_wizard.verify_ns()
    assert r["ok"] is True
    assert len(r["actual"]) == 2


def test_verify_ns_google_false(monkeypatch):
    monkeypatch.setattr(network_probe, "_run",
                        lambda cmd, timeout=8: (0, "ns-cloud-e1.googledomains.com.\nns-cloud-e2.googledomains.com.\n", ""))
    r = network_wizard.verify_ns()
    assert r["ok"] is False


def test_verify_baza_dns(monkeypatch):
    monkeypatch.setattr(network_probe, "_run",
                        lambda cmd, timeout=8: (0, "104.21.0.1\n", ""))
    r = network_wizard.verify_baza_dns()
    assert r["ok"] is True
    monkeypatch.setattr(network_probe, "_run",
                        lambda cmd, timeout=8: (0, "\n", ""))
    r2 = network_wizard.verify_baza_dns()
    assert r2["ok"] is False


def test_verify_email_reuses_probe_dns(monkeypatch):
    monkeypatch.setattr(network_probe, "probe_dns", lambda: [
        {"name": "ahb123.com", "rtype": "MX", "actual": ["1 smtp.google.com"], "ok": True},
        {"name": "ahb123.com", "rtype": "TXT", "actual": ["v=spf1 include:_spf.google.com ~all"], "ok": True},
        {"name": "google._domainkey.ahb123.com", "rtype": "TXT", "actual": ["v=DKIM1; k=rsa; p=abc"], "ok": True},
    ])
    r = network_wizard.verify_email()
    assert r["ok"] is True
    assert r["mx"] is True and r["spf"] is True and r["dkim"] is True


def test_verify_email_missing_dkim(monkeypatch):
    monkeypatch.setattr(network_probe, "probe_dns", lambda: [
        {"name": "ahb123.com", "rtype": "MX", "actual": ["1 smtp.google.com"], "ok": True},
        {"name": "ahb123.com", "rtype": "TXT", "actual": ["v=spf1 include:_spf.google.com ~all"], "ok": True},
        {"name": "google._domainkey.ahb123.com", "rtype": "TXT", "actual": [], "ok": False},
    ])
    r = network_wizard.verify_email()
    assert r["ok"] is False
    assert r["dkim"] is False


# ─── wiz_write_config: UUID extraction + yaml write ───────────────────────────

_TUNNEL_LIST = """\
You can obtain more detailed information for each tunnel with `cloudflared tunnel info <name/uuid>`.
ID                                   NAME            CREATED              CONNECTIONS
6ff42ae2-765d-4adf-8112-31c55c1551ef baza-dashboard  2026-07-02T10:00:00Z
"""

# Multi-tunnel fixture: stale tunnel listed FIRST with a different UUID, then baza-dashboard.
_TUNNEL_LIST_MULTI = """\
You can obtain more detailed information for each tunnel with `cloudflared tunnel info <name/uuid>`.
ID                                   NAME            CREATED              CONNECTIONS
aabbccdd-1111-2222-3333-444455556666 stale-tunnel    2025-01-01T00:00:00Z
6ff42ae2-765d-4adf-8112-31c55c1551ef baza-dashboard  2026-07-02T10:00:00Z
"""


def test_wiz_write_config_extracts_uuid_and_writes(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(network_probe, "_run",
                        lambda cmd, timeout=10: (0, _TUNNEL_LIST, ""))
    # inject home dir so no real ~/.cloudflared is touched
    rc, out, err = network_wizard.wiz_write_config({}, db_path=None, home=str(home))
    assert rc == 0
    cfg = home / ".cloudflared" / "config.yml"
    assert cfg.exists()
    text = cfg.read_text()
    assert "hostname: baza.ahb123.com" in text
    assert "6ff42ae2-765d-4adf-8112-31c55c1551ef" in text
    assert "http://localhost:8888" in text
    assert "http_status:404" in text
    # credentials-file must reference the injected home, not hardcoded /home/switchhacker
    assert str(home) in text
    assert "/home/switchhacker" not in text


def test_wiz_write_config_multi_tunnel_picks_baza_dashboard(tmp_path, monkeypatch):
    """When multiple tunnels are listed, wiz_write_config must pick the baza-dashboard
    UUID, not the first UUID in the output (which belongs to a stale tunnel)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(network_probe, "_run",
                        lambda cmd, timeout=10: (0, _TUNNEL_LIST_MULTI, ""))
    rc, out, err = network_wizard.wiz_write_config({}, db_path=None, home=str(home))
    assert rc == 0, f"expected success but got err={err!r}"
    cfg = home / ".cloudflared" / "config.yml"
    assert cfg.exists()
    text = cfg.read_text()
    # Must use the baza-dashboard UUID
    assert "6ff42ae2-765d-4adf-8112-31c55c1551ef" in text
    # Must NOT use the stale-tunnel UUID
    assert "aabbccdd-1111-2222-3333-444455556666" not in text
    # credentials-file must also reference the baza-dashboard UUID under injected home
    assert str(home) in text
    assert "6ff42ae2-765d-4adf-8112-31c55c1551ef.json" in text


def test_wiz_write_config_no_baza_dashboard_line(tmp_path, monkeypatch):
    """When no baza-dashboard row exists in tunnel list, wiz_write_config must return
    rc!=0 with an appropriate error and must NOT write config.yml."""
    home = tmp_path / "home"
    home.mkdir()
    tunnel_list_no_baza = """\
You can obtain more detailed information for each tunnel with `cloudflared tunnel info <name/uuid>`.
ID                                   NAME            CREATED              CONNECTIONS
aabbccdd-1111-2222-3333-444455556666 stale-tunnel    2025-01-01T00:00:00Z
"""
    monkeypatch.setattr(network_probe, "_run",
                        lambda cmd, timeout=10: (0, tunnel_list_no_baza, ""))
    rc, out, err = network_wizard.wiz_write_config({}, db_path=None, home=str(home))
    assert rc != 0
    assert "baza-dashboard" in err
    cfg = home / ".cloudflared" / "config.yml"
    assert not cfg.exists(), "config.yml must NOT be written when baza-dashboard tunnel is absent"


def test_wiz_write_config_no_uuid(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(network_probe, "_run",
                        lambda cmd, timeout=10: (0, "no tunnels found\n", ""))
    rc, out, err = network_wizard.wiz_write_config({}, db_path=None, home=str(home))
    assert rc != 0
    assert "uuid" in err.lower() or "tunnel" in err.lower()


# ─── run-actions registered in network_ops.ACTIONS as fn-style ────────────────

def test_wizard_actions_registered():
    for key in ("wiz_tunnel_create", "wiz_write_config", "wiz_route_dns", "wiz_install_service"):
        assert key in network_ops.ACTIONS
        assert "fn" in network_ops.ACTIONS[key]


def test_wiz_tunnel_create_audited(tmp_path, monkeypatch):
    db = str(tmp_path / "n.db")
    network_db_mod = network_ops.network_db
    network_db_mod.ensure_tables(db)
    # monkeypatch the ops _run so no real cloudflared is invoked
    monkeypatch.setattr(network_ops, "_run", lambda cmd, timeout=20: (1, "", "not authenticated"))
    res = network_ops.run_action("wiz_tunnel_create", {}, db_path=db)
    assert res["action"] == "wiz_tunnel_create"
    assert res["ok"] is False   # graceful failure, no exception
    rows = network_db_mod.recent_audit(db_path=db)
    assert rows[0]["action"] == "wiz_tunnel_create"
