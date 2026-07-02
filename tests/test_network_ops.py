"""Tests for dashboard/network_ops.py — Task 4 (Network tab action whitelist)."""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))

import pytest
import network_db
import network_ops


# ─── core whitelist + audit tests (from brief, exact) ─────────────────────────

def test_whitelist_and_audit(tmp_path, monkeypatch):
    db = str(tmp_path / "n.db")
    network_db.ensure_tables(db)

    # Unknown action key → ValueError, but audited first (rc=-2, "rejected")
    with pytest.raises(ValueError):
        network_ops.run_action("rm_rf", {}, db_path=db)
    recent = network_db.recent_audit(db_path=db)
    assert recent[0]["action"] == "rm_rf"
    assert recent[0]["rc"] == -2
    assert "rejected" in recent[0]["err"]

    # svc with unit not in UNITS → ValueError, but audited first
    with pytest.raises(ValueError):
        network_ops.run_action("svc", {"unit": "evil.service", "verb": "stop"}, db_path=db)
    recent = network_db.recent_audit(db_path=db)
    assert recent[0]["action"] == "svc"
    assert recent[0]["rc"] == -2
    assert "rejected" in recent[0]["err"]

    # nic with malicious name → ValueError, but audited first
    with pytest.raises(ValueError):
        network_ops.run_action("nic", {"name": "eth0; rm -rf /", "verb": "down"}, db_path=db)
    recent = network_db.recent_audit(db_path=db)
    assert recent[0]["action"] == "nic"
    assert recent[0]["rc"] == -2
    assert "rejected" in recent[0]["err"]

    # Success case: monkeypatch _run → rc=0
    calls = []
    monkeypatch.setattr(network_ops, "_run", lambda cmd, timeout=20: (calls.append(cmd) or (0, "ok", "")))
    r = network_ops.run_action("svc", {"unit": "caddy.service", "verb": "restart"}, db_path=db)
    assert r["ok"] and calls[0] == ["sudo", "-n", "systemctl", "restart", "caddy.service"]
    assert network_db.recent_audit(db_path=db)[0]["action"] == "svc"

    # Failure case: monkeypatch _run → rc=1, still audited
    monkeypatch.setattr(network_ops, "_run", lambda cmd, timeout=20: (1, "", "boom"))
    r = network_ops.run_action("ts_down", {}, db_path=db)
    assert r["ok"] is False
    # Check that the most recent entry (ts_down failure) is in the audit
    assert network_db.recent_audit(db_path=db)[0]["action"] == "ts_down"
    assert network_db.recent_audit(db_path=db)[0]["rc"] == 1


def test_serve_argv():
    argv = network_ops.ACTIONS["ts_serve"]["argv"]({"mapping": "dash", "on": False})
    assert argv == ["sudo", "-n", "tailscale", "serve", "--https=443", "off"]


# ─── additional coverage ───────────────────────────────────────────────────────

def test_svc_risky_logic():
    """Stop of caddy or tailscaled is risky; restart of caddy is safe."""
    risk_fn = network_ops.ACTIONS["svc"].get("risk_fn")
    assert risk_fn({"unit": "caddy.service", "verb": "stop"}) == "risky"
    assert risk_fn({"unit": "snap.tailscale.tailscaled.service", "verb": "stop"}) == "risky"
    assert risk_fn({"unit": "caddy.service", "verb": "restart"}) == "safe"
    assert risk_fn({"unit": "baza-ddns.service", "verb": "stop"}) == "safe"


def test_ts_down_is_risky():
    risk = network_ops.ACTIONS["ts_down"].get("risk")
    assert risk == "risky"


def test_ts_up_is_safe():
    risk = network_ops.ACTIONS["ts_up"].get("risk")
    assert risk == "safe"


def test_ts_exit_node_argv():
    argv_on = network_ops.ACTIONS["ts_exit_node"]["argv"]({"on": True})
    assert argv_on == ["sudo", "-n", "tailscale", "set", "--advertise-exit-node=true"]
    argv_off = network_ops.ACTIONS["ts_exit_node"]["argv"]({"on": False})
    assert argv_off == ["sudo", "-n", "tailscale", "set", "--advertise-exit-node=false"]


def test_ts_serve_dash_on():
    argv = network_ops.ACTIONS["ts_serve"]["argv"]({"mapping": "dash", "on": True})
    assert argv == ["sudo", "-n", "tailscale", "serve", "--bg", "--https=443", "http://127.0.0.1:8888"]


def test_ts_serve_vision_on():
    argv = network_ops.ACTIONS["ts_serve"]["argv"]({"mapping": "vision", "on": True})
    assert argv == ["sudo", "-n", "tailscale", "serve", "--bg", "--https=8443", "http://localhost:8889"]


def test_ts_serve_vision_off():
    argv = network_ops.ACTIONS["ts_serve"]["argv"]({"mapping": "vision", "on": False})
    assert argv == ["sudo", "-n", "tailscale", "serve", "--https=8443", "off"]


def test_ts_serve_invalid_mapping():
    with pytest.raises(ValueError):
        network_ops.ACTIONS["ts_serve"]["argv"]({"mapping": "hacker", "on": True})


def test_nic_argv():
    argv = network_ops.ACTIONS["nic"]["argv"]({"name": "enp6s0", "verb": "up"})
    assert argv == ["sudo", "-n", "ip", "link", "set", "enp6s0", "up"]


def test_nic_invalid_name():
    with pytest.raises(ValueError):
        network_ops.ACTIONS["nic"]["argv"]({"name": "eth99", "verb": "up"})


def test_nic_invalid_verb():
    with pytest.raises(ValueError):
        network_ops.ACTIONS["nic"]["argv"]({"name": "enp6s0", "verb": "delete"})


def test_dhcp_renew_argv():
    argv = network_ops.ACTIONS["dhcp_renew"]["argv"]({"name": "enp7s0"})
    assert argv == ["sudo", "-n", "dhclient", "-v", "enp7s0"]


def test_dhcp_renew_timeout(tmp_path, monkeypatch):
    """dhcp_renew must pass timeout=30 to _run."""
    db = str(tmp_path / "n.db")
    network_db.ensure_tables(db)
    seen = {}
    def fake_run(cmd, timeout=20):
        seen["timeout"] = timeout
        return (0, "ok", "")
    monkeypatch.setattr(network_ops, "_run", fake_run)
    network_ops.run_action("dhcp_renew", {"name": "enp6s0"}, db_path=db)
    assert seen["timeout"] == 30


def test_ddns_run_argv():
    argv = network_ops.ACTIONS["ddns_run"]["argv"]({})
    assert argv == ["sudo", "-n", "systemctl", "start", "baza-ddns.service"]


def test_action_meta_structure():
    meta = network_ops.action_meta()
    assert isinstance(meta, list)
    keys = {m["key"] for m in meta}
    expected_keys = {
        "svc", "ddns_run", "ts_up", "ts_down", "ts_exit_node", "ts_serve", "nic", "dhcp_renew",
        "ddns_timer_enable", "ddns_timer_disable",
    }
    assert expected_keys == keys
    for m in meta:
        assert "key" in m and "desc" in m and "risk" in m


def test_run_result_shape(tmp_path, monkeypatch):
    """run_action returns all required keys."""
    db = str(tmp_path / "n.db")
    network_db.ensure_tables(db)
    monkeypatch.setattr(network_ops, "_run", lambda cmd, timeout=20: (0, "stdout", ""))
    r = network_ops.run_action("ddns_run", {}, db_path=db)
    assert set(r.keys()) >= {"ok", "rc", "out", "err", "action"}
    assert r["action"] == "ddns_run"


def test_svc_invalid_verb(tmp_path):
    db = str(tmp_path / "n.db")
    network_db.ensure_tables(db)
    with pytest.raises(ValueError):
        network_ops.run_action("svc", {"unit": "caddy.service", "verb": "kill"}, db_path=db)


# ─── Task 7: caddy_read / caddy_apply / caddy_rollback ────────────────────────

def test_caddy_apply_validate_fail_leaves_live_untouched(tmp_path, monkeypatch):
    """Validate-fail: returns ok=False/stage=validate; no cp or reload argv recorded."""
    db = str(tmp_path / "n.db")
    network_db.ensure_tables(db)

    recorded = []

    def fake_run(cmd, timeout=20, input_text=None):
        recorded.append(cmd)
        # validate call returns rc=1 (syntax error)
        if "validate" in cmd:
            return (1, "", "parse error: syntax error")
        return (0, "", "")

    monkeypatch.setattr(network_ops, "_run", fake_run)

    result = network_ops.caddy_apply("bad caddyfile text", db_path=db)

    assert result["ok"] is False
    assert result["stage"] == "validate"
    assert "parse error" in result["err"] or "syntax error" in result["err"]

    # No cp or reload should have been called after the failed validate
    post_validate = recorded[recorded.index(next(c for c in recorded if "validate" in c)) + 1:]
    cp_or_reload = [c for c in post_validate if any(x in c for x in ["cp", "reload"])]
    assert cp_or_reload == [], f"cp/reload called after validate failure: {cp_or_reload}"

    # Audit row written even on failure
    rows = network_db.recent_audit(db_path=db)
    assert any(r["action"] == "caddy_apply" for r in rows)


def test_caddy_apply_happy_path_argv_sequence(tmp_path, monkeypatch):
    """Happy path: argv sequence = tee-staged → validate → cp-bak → cp-live → reload, audit written."""
    db = str(tmp_path / "n.db")
    network_db.ensure_tables(db)

    recorded = []

    def fake_run(cmd, timeout=20, input_text=None):
        recorded.append(cmd)
        return (0, "", "")

    monkeypatch.setattr(network_ops, "_run", fake_run)

    result = network_ops.caddy_apply("good caddyfile text", db_path=db)

    assert result["ok"] is True
    assert result["stage"] == "done"

    # Verify argv sequence:
    # 1. tee to staged
    assert any("tee" in c and ".Caddyfile.staged" in " ".join(c) for c in recorded), \
        f"tee staged not found: {recorded}"
    # 2. validate
    assert any("validate" in c for c in recorded), f"validate not found: {recorded}"
    # 3. cp bak (backup of live)
    bak_cps = [c for c in recorded if "cp" in c and ".bak." in " ".join(c)]
    assert bak_cps, f"cp-bak not found: {recorded}"
    # 4. cp staged → live
    live_cps = [c for c in recorded if "cp" in c and ".Caddyfile.staged" in " ".join(c)]
    assert live_cps, f"cp-live (staged→Caddyfile) not found: {recorded}"
    # 5. reload caddy
    assert any("reload" in c for c in recorded), f"reload not found: {recorded}"

    # Check order: tee < validate < cp-bak < cp-live < reload
    def idx(pred):
        for i, c in enumerate(recorded):
            if pred(c):
                return i
        return 9999

    i_tee      = idx(lambda c: "tee" in c and ".Caddyfile.staged" in " ".join(c))
    i_validate = idx(lambda c: "validate" in c)
    i_bak      = idx(lambda c: "cp" in c and ".bak." in " ".join(c))
    i_cp_live  = idx(lambda c: "cp" in c and ".Caddyfile.staged" in " ".join(c))
    i_reload   = idx(lambda c: "reload" in c)

    assert i_tee < i_validate < i_bak < i_cp_live < i_reload, \
        f"Wrong order: {i_tee=} {i_validate=} {i_bak=} {i_cp_live=} {i_reload=}"

    # Audit row written
    rows = network_db.recent_audit(db_path=db)
    assert any(r["action"] == "caddy_apply" and r["rc"] == 0 for r in rows)


def test_caddy_rollback_traversal_rejected():
    """caddy_rollback with path-traversal name raises ValueError."""
    with pytest.raises(ValueError):
        network_ops.caddy_rollback("../../etc/passwd")
    with pytest.raises(ValueError):
        network_ops.caddy_rollback("Caddyfile.bak.20260101-120000/../../../etc/passwd")
    with pytest.raises(ValueError):
        network_ops.caddy_rollback("notabackup")
    with pytest.raises(ValueError):
        network_ops.caddy_rollback("")


def test_caddy_rollback_valid_name_happy(tmp_path, monkeypatch):
    """caddy_rollback with valid name: same validate→bak-current→cp→reload pipeline."""
    db = str(tmp_path / "n.db")
    network_db.ensure_tables(db)

    recorded = []

    def fake_run(cmd, timeout=20, input_text=None):
        recorded.append(cmd)
        return (0, "", "")

    monkeypatch.setattr(network_ops, "_run", fake_run)

    result = network_ops.caddy_rollback("Caddyfile.bak.20260101-120000", db_path=db)

    assert result["ok"] is True
    # Must have: cp backup→staged, validate, cp bak-current, cp-live, reload
    assert any("validate" in c for c in recorded), "validate not called"
    assert any("reload" in c for c in recorded), "reload not called"

    rows = network_db.recent_audit(db_path=db)
    assert any(r["action"] == "caddy_rollback" and r["rc"] == 0 for r in rows)


def test_caddy_apply_empty_text_rejected(tmp_path, monkeypatch):
    """caddy_apply with empty text should return ok=False and write audit row with rc=-2."""
    db = str(tmp_path / "n.db")
    network_db.ensure_tables(db)

    # Monkeypatch _run to track calls (should not be called for empty text)
    calls = []
    def fake_run(cmd, timeout=20, input_text=None):
        calls.append(cmd)
        return (0, "", "")

    monkeypatch.setattr(network_ops, "_run", fake_run)

    result = network_ops.caddy_apply("", db_path=db)
    assert result["ok"] is False
    assert result["stage"] == "validate"

    # _run should NOT have been called (early return before any sudo)
    assert calls == [], f"_run should not be called for empty text, but was: {calls}"

    # Audit row should be written with rc=-2
    rows = network_db.recent_audit(db_path=db)
    assert len(rows) > 0, "No audit rows found"
    assert rows[0]["action"] == "caddy_apply"
    assert rows[0]["rc"] == -2
    assert "rejected" in rows[0]["err"] and "empty text" in rows[0]["err"]


def test_caddy_read_returns_structure(monkeypatch):
    """caddy_read returns dict with path, text, backups keys."""
    def fake_run(cmd, timeout=20, input_text=None):
        if "cat" in cmd:
            return (0, "# fake caddyfile\n", "")
        if "find" in cmd:
            return (0, "Caddyfile.bak.20260101-120000\nCaddyfile.bak.20260102-130000\n", "")
        return (0, "", "")

    monkeypatch.setattr(network_ops, "_run", fake_run)

    result = network_ops.caddy_read()
    assert "path" in result
    assert "text" in result
    assert "backups" in result
    assert isinstance(result["backups"], list)

    # Verify find command was used (not bash/ls)
    # Can't directly verify without deeper inspection, but the test passes if it returns backups


# ─── Task 9: ddns_timer_enable / ddns_timer_disable ──────────────────────────

_TIMER_PATH = "/etc/systemd/system/baza-ddns.timer"


def test_ddns_timer_enable_writes_unit_when_missing(tmp_path, monkeypatch):
    """When the timer unit file is missing, tee+daemon-reload+enable --now are called."""
    db = str(tmp_path / "n.db")
    network_db.ensure_tables(db)

    # File does NOT exist
    monkeypatch.setattr("network_ops.os.path.exists", lambda p: False)

    recorded = []
    def fake_run(cmd, timeout=20, input_text=None):
        recorded.append((cmd, input_text))
        return (0, "ok", "")

    monkeypatch.setattr(network_ops, "_run", fake_run)

    result = network_ops.run_action("ddns_timer_enable", {}, db_path=db)

    assert result["ok"] is True

    # tee must have been called with the unit content
    tee_calls = [(c, t) for c, t in recorded if "tee" in c and _TIMER_PATH in c]
    assert tee_calls, f"tee to {_TIMER_PATH} not called; recorded={recorded}"
    tee_input = tee_calls[0][1]
    assert "baza-ddns" in tee_input or "DDNS" in tee_input
    assert "OnCalendar=hourly" in tee_input
    assert "RandomizedDelaySec=300" in tee_input

    # daemon-reload must be present
    daemon_reload = [c for c, _ in recorded if "daemon-reload" in c]
    assert daemon_reload, f"daemon-reload not called; recorded={recorded}"

    # enable --now must be present
    enable_cmds = [c for c, _ in recorded if "enable" in c and "--now" in c and "baza-ddns.timer" in c]
    assert enable_cmds, f"enable --now not called; recorded={recorded}"

    # Audit row written
    rows = network_db.recent_audit(db_path=db)
    assert any(r["action"] == "ddns_timer_enable" for r in rows)


def test_ddns_timer_enable_skips_tee_when_file_exists(tmp_path, monkeypatch):
    """When the timer unit file already exists, tee is NOT called but daemon-reload+enable are."""
    db = str(tmp_path / "n.db")
    network_db.ensure_tables(db)

    # File DOES exist
    monkeypatch.setattr("network_ops.os.path.exists", lambda p: True)

    recorded = []
    def fake_run(cmd, timeout=20, input_text=None):
        recorded.append((cmd, input_text))
        return (0, "ok", "")

    monkeypatch.setattr(network_ops, "_run", fake_run)

    result = network_ops.run_action("ddns_timer_enable", {}, db_path=db)

    assert result["ok"] is True

    # tee must NOT have been called
    tee_calls = [(c, t) for c, t in recorded if "tee" in c and _TIMER_PATH in c]
    assert not tee_calls, f"tee should not be called when file exists; got: {tee_calls}"

    # daemon-reload and enable --now still called
    daemon_reload = [c for c, _ in recorded if "daemon-reload" in c]
    assert daemon_reload, "daemon-reload should still be called"
    enable_cmds = [c for c, _ in recorded if "enable" in c and "--now" in c and "baza-ddns.timer" in c]
    assert enable_cmds, "enable --now should still be called"


def test_ddns_timer_disable_disables(tmp_path, monkeypatch):
    """ddns_timer_disable calls disable --now baza-ddns.timer and audits."""
    db = str(tmp_path / "n.db")
    network_db.ensure_tables(db)

    recorded = []
    def fake_run(cmd, timeout=20, input_text=None):
        recorded.append((cmd, input_text))
        return (0, "ok", "")

    monkeypatch.setattr(network_ops, "_run", fake_run)

    result = network_ops.run_action("ddns_timer_disable", {}, db_path=db)

    assert result["ok"] is True
    disable_cmds = [c for c, _ in recorded if "disable" in c and "--now" in c and "baza-ddns.timer" in c]
    assert disable_cmds, f"disable --now not called; recorded={recorded}"

    # Audit row written
    rows = network_db.recent_audit(db_path=db)
    assert any(r["action"] == "ddns_timer_disable" for r in rows)


def test_ddns_timer_enable_failure_audited(tmp_path, monkeypatch):
    """If one of the commands fails, ok=False and the audit row is written."""
    db = str(tmp_path / "n.db")
    network_db.ensure_tables(db)

    monkeypatch.setattr("network_ops.os.path.exists", lambda p: False)

    call_count = [0]
    def fake_run(cmd, timeout=20, input_text=None):
        call_count[0] += 1
        # fail on tee
        if "tee" in cmd:
            return (1, "", "permission denied")
        return (0, "ok", "")

    monkeypatch.setattr(network_ops, "_run", fake_run)

    result = network_ops.run_action("ddns_timer_enable", {}, db_path=db)
    assert result["ok"] is False

    rows = network_db.recent_audit(db_path=db)
    assert any(r["action"] == "ddns_timer_enable" for r in rows)


def test_action_meta_includes_timer_actions():
    """action_meta must include ddns_timer_enable and ddns_timer_disable."""
    meta = network_ops.action_meta()
    keys = {m["key"] for m in meta}
    assert "ddns_timer_enable" in keys
    assert "ddns_timer_disable" in keys
