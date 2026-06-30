"""Tests for hardware_probe — the pure parse/diff logic behind baseline & verify."""
import os
import sys

DASHBOARD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, DASHBOARD_DIR)

import hardware_probe as hp


# ---------------- parse_systemctl_units ----------------

def test_parse_units_basic():
    raw = (
        "baza-dashboard.service loaded active running Baza Dashboard\n"
        "baza-tool-server.service loaded active running Baza Tool Server\n"
        "baza-agent-claw-batto.service loaded active running Claw\n"
    )
    units = hp.parse_systemctl_units(raw)
    assert len(units) == 3
    assert units[0]["unit"] == "baza-dashboard.service"
    assert units[0]["active"] == "active"
    assert units[0]["sub"] == "running"


def test_parse_units_strips_bullet_and_blank():
    raw = (
        "\n"
        "* baza-litellm.service loaded failed failed LiteLLM\n"
        "  baza-sd-webui.service loaded active running SD WebUI\n"
    )
    units = hp.parse_systemctl_units(raw)
    assert len(units) == 2
    failed = [u for u in units if u["unit"] == "baza-litellm.service"][0]
    assert failed["active"] == "failed"


def test_parse_units_ignores_non_service_and_short_lines():
    raw = (
        "baza-watchdog.timer loaded active waiting Watchdog Timer\n"
        "garbage line\n"
        "baza-dashboard.service loaded active running Baza Dashboard\n"
    )
    units = hp.parse_systemctl_units(raw)
    assert len(units) == 1
    assert units[0]["unit"] == "baza-dashboard.service"


# ---------------- check status helper ----------------

def test_unit_status_ok_for_active_running():
    assert hp.unit_status("active", "running") == "ok"


def test_unit_status_ok_for_oneshot_exited():
    # oneshot services finish as active/exited — still healthy
    assert hp.unit_status("active", "exited") == "ok"


def test_unit_status_fail_for_failed():
    assert hp.unit_status("failed", "failed") == "fail"


def test_unit_status_fail_for_inactive_dead():
    assert hp.unit_status("inactive", "dead") == "fail"


# ---------------- diff_snapshots ----------------

def _snap(services):
    return {
        "captured_at": "2026-06-29T20:00:00",
        "domains": {
            "services": {"checks": services},
            "ollama_gpu": {"checks": []},
            "datastores": {"checks": []},
            "network": {"checks": []},
            "firmware": {"checks": []},
        },
    }


def test_diff_all_healthy_passes():
    base = _snap([{"name": "baza-dashboard.service", "status": "ok", "detail": "active/running"}])
    cur = _snap([{"name": "baza-dashboard.service", "status": "ok", "detail": "active/running"}])
    d = hp.diff_snapshots(base, cur)
    assert d["pass"] is True
    assert d["regressions"] == []


def test_diff_detects_regression():
    base = _snap([{"name": "baza-litellm.service", "status": "ok", "detail": "active/running"}])
    cur = _snap([{"name": "baza-litellm.service", "status": "fail", "detail": "failed/failed"}])
    d = hp.diff_snapshots(base, cur)
    assert d["pass"] is False
    assert len(d["regressions"]) == 1
    reg = d["regressions"][0]
    assert reg["name"] == "baza-litellm.service"
    assert reg["domain"] == "services"
    assert reg["was"] == "ok"
    assert reg["now"] == "fail"


def test_diff_missing_check_is_regression():
    # something that was healthy is now entirely absent (unit vanished)
    base = _snap([{"name": "baza-agent-rex-valor.service", "status": "ok", "detail": "active/running"}])
    cur = _snap([])
    d = hp.diff_snapshots(base, cur)
    assert d["pass"] is False
    assert d["regressions"][0]["now"] == "missing"


def test_diff_recovered_not_a_regression():
    base = _snap([{"name": "baza-sd-webui.service", "status": "fail", "detail": "inactive/dead"}])
    cur = _snap([{"name": "baza-sd-webui.service", "status": "ok", "detail": "active/running"}])
    d = hp.diff_snapshots(base, cur)
    assert d["pass"] is True
    assert d["regressions"] == []
    assert len(d["recovered"]) == 1


def test_diff_firmware_change_is_not_regression_but_surfaced():
    base = {
        "captured_at": "t0",
        "domains": {
            "services": {"checks": []},
            "ollama_gpu": {"checks": []},
            "datastores": {"checks": []},
            "network": {"checks": []},
            "firmware": {"checks": [{"name": "bios_version", "status": "info", "detail": "5013"}]},
        },
    }
    cur = {
        "captured_at": "t1",
        "domains": {
            "services": {"checks": []},
            "ollama_gpu": {"checks": []},
            "datastores": {"checks": []},
            "network": {"checks": []},
            "firmware": {"checks": [{"name": "bios_version", "status": "info", "detail": "5021"}]},
        },
    }
    d = hp.diff_snapshots(base, cur)
    assert d["pass"] is True  # firmware change must NOT fail verification
    assert any(c["name"] == "bios_version" and c["now_detail"] == "5021" for c in d["changes"])


def test_diff_idle_now_is_not_a_regression():
    # baseline caught a timer-oneshot mid-run (ok); now it's idle between firings
    base = _snap([{"name": "baza-backup.service", "status": "ok", "detail": "active/running"}])
    cur = _snap([{"name": "baza-backup.service", "status": "idle", "detail": "inactive/dead"}])
    d = hp.diff_snapshots(base, cur)
    assert d["pass"] is True
    assert d["regressions"] == []


def test_summary_counts_idle():
    snap = _snap([
        {"name": "a.service", "status": "ok", "detail": ""},
        {"name": "b.service", "status": "idle", "detail": ""},
    ])
    s = hp.summarize(snap)
    assert s["services"]["idle"] == 1
    assert s["services"]["ok"] == 1


def test_summary_counts():
    snap = _snap([
        {"name": "a.service", "status": "ok", "detail": ""},
        {"name": "b.service", "status": "fail", "detail": ""},
        {"name": "c.service", "status": "ok", "detail": ""},
    ])
    s = hp.summarize(snap)
    assert s["services"]["ok"] == 2
    assert s["services"]["fail"] == 1
    assert s["services"]["total"] == 3
