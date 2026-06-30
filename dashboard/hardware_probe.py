"""System-state probe for the Hardware & Upgrades feature.

Single source of truth for "what does baza look like right now". The same probe
is used to (1) capture a known-good *baseline* before a hardware upgrade and
(2) *verify* the box came back healthy after reboot — verification is just a
diff of the two snapshots.

Command execution (`_run`, the `probe_*` collectors) is deliberately separated
from the pure parse/diff logic (`parse_systemctl_units`, `unit_status`,
`diff_snapshots`, `summarize`) so the bug-prone logic is unit-testable with
injected fixtures and never has to shell out in a test.

Design note: the service list is *auto-discovered* (`systemctl list-units
'baza-*'`), never hardcoded — so it stays accurate as units come and go, and an
agent that runs on phantom (e.g. specter_voss) correctly shows up as absent
locally rather than as a phantom "missing" unit.
"""
import json
import socket
import subprocess
import urllib.request
from datetime import datetime

OLLAMA_PORTS = [11434, 11435, 11436, 11437, 11438]
DOMAINS = ["services", "ollama_gpu", "datastores", "network", "firmware"]


# ───────────────────────── command runner ──────────────────────────

def _run(cmd, timeout=10):
    """Run a command, return (rc, stdout, stderr). Never raises."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:  # noqa: BLE001 — probe must never crash the caller
        return -1, "", str(e)


# ───────────────────────── pure parse / status ─────────────────────

def parse_systemctl_units(raw):
    """Parse `systemctl list-units --type=service --all --plain --no-legend`.

    Returns a list of {unit, load, active, sub}. Ignores blank lines, leading
    status bullets (●/*/○), short/garbage lines, and non-.service rows.
    """
    units = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if line[0] in ("●", "*", "○"):  # ● * ○
            line = line[1:].strip()
        parts = line.split()
        if len(parts) < 4:
            continue
        unit, load, active, sub = parts[0], parts[1], parts[2], parts[3]
        if not unit.endswith(".service"):
            continue
        units.append({"unit": unit, "load": load, "active": active, "sub": sub})
    return units


def unit_status(active, sub):
    """Map a systemd (active, sub) pair to our ok/fail status."""
    if active == "active":
        return "ok"  # running, or exited (oneshot) — both healthy
    if active == "activating":
        return "warn"
    return "fail"  # failed, inactive/dead, etc.


def _check(name, status, detail=""):
    return {"name": name, "status": status, "detail": detail}


def summarize(snapshot):
    """Per-domain {ok, fail, warn, idle, total} counts."""
    out = {}
    for dom, data in snapshot.get("domains", {}).items():
        ok = fail = warn = idle = 0
        for c in data.get("checks", []):
            st = c.get("status")
            if st == "ok":
                ok += 1
            elif st == "fail":
                fail += 1
            elif st == "warn":
                warn += 1
            elif st == "idle":
                idle += 1
        out[dom] = {"ok": ok, "fail": fail, "warn": warn, "idle": idle,
                    "total": len(data.get("checks", []))}
    return out


def diff_snapshots(baseline, current):
    """Diff a baseline snapshot against a current one.

    A *regression* is any non-firmware check that was `ok` in the baseline and
    is no longer `ok` now (including vanished checks → `now == "missing"`).
    Firmware checks are informational: a BIOS/CPU *change* is the whole point of
    an upgrade, so firmware detail changes are surfaced under `changes` but never
    counted as regressions. Verify PASSES iff there are zero regressions.
    """
    def index(snap):
        idx = {}
        for dom, data in snap.get("domains", {}).items():
            for c in data.get("checks", []):
                idx[(dom, c["name"])] = c
        return idx

    bidx, cidx = index(baseline), index(current)
    regressions, recovered, changes = [], [], []

    for key, bcheck in bidx.items():
        dom, name = key
        ccheck = cidx.get(key)
        if dom == "firmware":
            now_detail = ccheck["detail"] if ccheck else "missing"
            if (bcheck.get("detail") or "") != (now_detail or ""):
                changes.append({"domain": dom, "name": name,
                                "was_detail": bcheck.get("detail", ""),
                                "now_detail": now_detail})
            continue
        was = bcheck.get("status")
        now = ccheck.get("status") if ccheck else "missing"
        # A regression is a once-healthy check that is now hard-down or vanished.
        # `idle`/`warn` are not regressions: idle = a timer-backed oneshot between
        # runs (would otherwise race-flag if the baseline caught it mid-run).
        if was == "ok" and now in ("fail", "missing"):
            regressions.append({"domain": dom, "name": name, "was": was, "now": now,
                                "detail": ccheck.get("detail", "") if ccheck else ""})
        elif was != "ok" and now == "ok":
            recovered.append({"domain": dom, "name": name, "was": was, "now": now})

    return {
        "pass": len(regressions) == 0,
        "regressions": regressions,
        "recovered": recovered,
        "changes": changes,
        "summary": summarize(current),
        "baseline_at": baseline.get("captured_at"),
        "current_at": current.get("captured_at"),
    }


# ───────────────────────── domain collectors ───────────────────────

def _timer_backed_services():
    """Set of service unit names that are triggered by a baza-* timer.

    Such services sit inactive/dead between firings — that's `idle`, not a
    failure. baza-foo.timer → baza-foo.service.
    """
    rc, out, _ = _run(["systemctl", "list-units", "baza-*", "--type=timer",
                       "--all", "--plain", "--no-legend", "--no-pager"])
    backed = set()
    for line in out.splitlines():
        parts = line.strip().lstrip("●*○ ").split()
        if parts and parts[0].endswith(".timer"):
            backed.add(parts[0][:-len(".timer")] + ".service")
    return backed


def probe_services():
    """All baza-* system units + baza-claw-* user units, auto-discovered."""
    checks = []
    timer_backed = _timer_backed_services()
    rc, out, _ = _run(["systemctl", "list-units", "baza-*", "--type=service",
                       "--all", "--plain", "--no-legend", "--no-pager"])
    for u in parse_systemctl_units(out):
        st = unit_status(u["active"], u["sub"])
        # A timer-backed oneshot that's down between runs is idle, not failed.
        if st == "fail" and u["unit"] in timer_backed and u["active"] != "failed":
            st = "idle"
        checks.append(_check(u["unit"], st, f"{u['active']}/{u['sub']}"))
    # user-scope claw review units
    rc, out, _ = _run(["systemctl", "--user", "list-units", "baza-claw-*",
                       "--type=service", "--all", "--plain", "--no-legend", "--no-pager"])
    for u in parse_systemctl_units(out):
        checks.append(_check(u["unit"] + " (user)", unit_status(u["active"], u["sub"]),
                             f"{u['active']}/{u['sub']}"))
    return {"checks": checks}


def _http_ok(url, timeout=4):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 500
    except Exception:
        return False


def probe_ollama_gpu():
    checks = []
    for port in OLLAMA_PORTS:
        ok = _http_ok(f"http://127.0.0.1:{port}/api/tags")
        checks.append(_check(f"ollama:{port}", "ok" if ok else "fail",
                             "responding" if ok else "no response"))
    # NVIDIA RTX 3070
    rc, out, _ = _run(["nvidia-smi", "-L"])
    nv_ok = rc == 0 and "GPU 0" in out
    checks.append(_check("gpu_nvidia", "ok" if nv_ok else "fail",
                         out.strip().splitlines()[0] if nv_ok and out.strip() else "not detected"))
    # AMD RX 6700 XT via vulkaninfo
    rc, out, _ = _run(["vulkaninfo", "--summary"], timeout=15)
    amd_ok = rc == 0 and ("Radeon" in out or "AMD" in out)
    checks.append(_check("gpu_amd", "ok" if amd_ok else "warn",
                         "detected" if amd_ok else "not detected via vulkaninfo"))
    return {"checks": checks}


def probe_datastores():
    checks = []
    rc, out, _ = _run(["zpool", "list", "-H", "-o", "name,health", "empirepool"])
    pool_ok = rc == 0 and "empirepool" in out and "ONLINE" in out
    checks.append(_check("zpool:empirepool", "ok" if pool_ok else "fail",
                         out.strip() or "not imported"))
    rc, out, _ = _run(["zfs", "list", "-H", "-o", "mountpoint", "empirepool"])
    mounted = rc == 0 and "/mnt/empirepool" in out
    checks.append(_check("empirepool_mounted", "ok" if mounted else "fail",
                         out.strip() or "not mounted"))
    # Redis
    redis_ok = _port_open("127.0.0.1", 6379)
    checks.append(_check("redis:6379", "ok" if redis_ok else "fail",
                         "reachable" if redis_ok else "unreachable"))
    # Postgres
    pg_ok = _port_open("127.0.0.1", 5432)
    checks.append(_check("postgres:5432", "ok" if pg_ok else "fail",
                         "reachable" if pg_ok else "unreachable"))
    return {"checks": checks}


def _port_open(host, port, timeout=3):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def probe_network():
    checks = []
    rc, out, _ = _run(["systemctl", "list-timers", "baza-*", "--all", "--no-legend", "--no-pager"])
    active_timers = [ln for ln in out.splitlines() if ".timer" in ln]
    checks.append(_check("baza_timers", "ok" if active_timers else "warn",
                         f"{len(active_timers)} timers"))
    rc, out, _ = _run(["tailscale", "status"])
    ts_ok = rc == 0 and out.strip() != ""
    checks.append(_check("tailscale", "ok" if ts_ok else "warn",
                         "up" if ts_ok else "down/unknown"))
    rc, out, err = _run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                         "phantom", "true"], timeout=12)
    checks.append(_check("phantom_reachable", "ok" if rc == 0 else "warn",
                         "reachable" if rc == 0 else (err.strip()[:60] or "unreachable")))
    return {"checks": checks}


def _dmidecode(field):
    """dmidecode needs root; fall back to passwordless sudo, else give up cleanly."""
    rc, out, _ = _run(["dmidecode", "-s", field])
    if rc != 0 or not out.strip():
        rc, out, _ = _run(["sudo", "-n", "dmidecode", "-s", field])
    return out.strip().splitlines()[0] if (rc == 0 and out.strip()) else "unknown"


def probe_firmware():
    """Informational: BIOS / CPU / kernel. Changes here are EXPECTED on upgrade
    and are surfaced (not failed) by diff_snapshots."""
    checks = []
    checks.append(_check("bios_version", "info", _dmidecode("bios-version")))
    checks.append(_check("motherboard", "info", _dmidecode("baseboard-product-name")))
    rc, out, _ = _run(["bash", "-lc", "lscpu | awk -F: '/Model name/{print $2; exit}'"])
    cpu = out.strip() if (rc == 0 and out.strip()) else "unknown"
    checks.append(_check("cpu_model", "info", cpu))
    rc, out, _ = _run(["uname", "-r"])
    checks.append(_check("kernel", "info", out.strip() or "unknown"))
    return {"checks": checks}


def probe_system():
    """Full snapshot across all domains."""
    return {
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "domains": {
            "services": probe_services(),
            "ollama_gpu": probe_ollama_gpu(),
            "datastores": probe_datastores(),
            "network": probe_network(),
            "firmware": probe_firmware(),
        },
    }


if __name__ == "__main__":
    print(json.dumps(probe_system(), indent=2))
