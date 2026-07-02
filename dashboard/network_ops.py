"""network_ops.py — action whitelist + executor for the Network tab.

Security boundary: every user-supplied parameter is validated against a strict
whitelist before touching any argv position.  Unknown keys and off-whitelist
values raise ValueError (routes map that to HTTP 400).

Every execution — success or failure — is persisted via network_db.audit().
"""
import os
import re
import subprocess
from datetime import datetime

# ── dependency imports (dual-path pattern used in this codebase) ──────────────
try:
    from dashboard import network_db
except ImportError:
    import network_db

try:
    from dashboard.network_probe import UNITS
except ImportError:
    try:
        from network_probe import UNITS
    except ImportError:
        UNITS = [
            "caddy.service",
            "snap.tailscale.tailscaled.service",
            "cloudflared.service",
            "baza-ddns.service",
            "openvpn.service",
        ]

# ── validation regexes ────────────────────────────────────────────────────────
VALID_NIC = re.compile(r"^(enp6s0|enp7s0|wlp5s0)$")

_VALID_SVC_VERB = {"start", "stop", "restart"}
_VALID_NIC_VERB = {"up", "down"}
_RISKY_SVC_UNITS = {"caddy.service", "snap.tailscale.tailscaled.service"}
_TS_SERVE_MAP = {
    "dash": {
        "port": "443",
        "target": "http://127.0.0.1:8888",
    },
    "vision": {
        "port": "8443",
        "target": "http://localhost:8889",
    },
}


# ── _run helper ───────────────────────────────────────────────────────────────

def _run(cmd, timeout=20, input_text=None):
    """Run argv list, return (rc, stdout, stderr).  Never raises.  shell=False.

    Optional input_text is passed as stdin (text mode).  Used for tee-based
    file writes (caddy staged config write).
    """
    try:
        kwargs = dict(capture_output=True, text=True, timeout=timeout, shell=False)
        if input_text is not None:
            kwargs["input"] = input_text
        r = subprocess.run(cmd, **kwargs)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:  # noqa: BLE001
        return -1, "", str(e)


# ── argv builders (each validates params and raises ValueError) ───────────────

def _argv_svc(params):
    unit = params.get("unit", "")
    verb = params.get("verb", "")
    if unit not in UNITS:
        raise ValueError(f"unit {unit!r} not in whitelist")
    if verb not in _VALID_SVC_VERB:
        raise ValueError(f"svc verb {verb!r} must be start/stop/restart")
    return ["sudo", "-n", "systemctl", verb, unit]


def _risk_svc(params):
    unit = params.get("unit", "")
    verb = params.get("verb", "")
    if verb == "stop" and unit in _RISKY_SVC_UNITS:
        return "risky"
    return "safe"


def _argv_ddns_run(_params):
    return ["sudo", "-n", "systemctl", "start", "baza-ddns.service"]


def _argv_ts_up(_params):
    return ["sudo", "-n", "tailscale", "up"]


def _argv_ts_down(_params):
    return ["sudo", "-n", "tailscale", "down"]


def _argv_ts_exit_node(params):
    on = params.get("on")
    if not isinstance(on, bool):
        raise ValueError("ts_exit_node requires 'on' as bool")
    flag = "--advertise-exit-node=true" if on else "--advertise-exit-node=false"
    return ["sudo", "-n", "tailscale", "set", flag]


def _argv_ts_serve(params):
    mapping = params.get("mapping", "")
    on = params.get("on")
    if mapping not in _TS_SERVE_MAP:
        raise ValueError(f"ts_serve mapping {mapping!r} must be one of {list(_TS_SERVE_MAP)}")
    if not isinstance(on, bool):
        raise ValueError("ts_serve requires 'on' as bool")
    cfg = _TS_SERVE_MAP[mapping]
    port_flag = f"--https={cfg['port']}"
    if on:
        return ["sudo", "-n", "tailscale", "serve", "--bg", port_flag, cfg["target"]]
    else:
        return ["sudo", "-n", "tailscale", "serve", port_flag, "off"]


def _risk_ts_serve(params):
    on = params.get("on")
    return "safe" if on else "risky"


def _argv_nic(params):
    name = params.get("name", "")
    verb = params.get("verb", "")
    if not VALID_NIC.match(name):
        raise ValueError(f"nic name {name!r} must match {VALID_NIC.pattern}")
    if verb not in _VALID_NIC_VERB:
        raise ValueError(f"nic verb {verb!r} must be up/down")
    return ["sudo", "-n", "ip", "link", "set", name, verb]


def _risk_nic(params):
    return "risky" if params.get("verb") == "down" else "safe"


def _argv_dhcp_renew(params):
    name = params.get("name", "")
    if not VALID_NIC.match(name):
        raise ValueError(f"dhcp_renew name {name!r} must match {VALID_NIC.pattern}")
    return ["sudo", "-n", "dhclient", "-v", name]


# ── ddns timer fn-style actions ───────────────────────────────────────────────

_TIMER_PATH = "/etc/systemd/system/baza-ddns.timer"

_TIMER_UNIT = """\
[Unit]
Description=baza DDNS — hourly WAN IP sync to Google Cloud DNS

[Timer]
OnCalendar=hourly
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
"""


_VALID_UFW_VERBS = {"allow", "deny", "delete-allow", "delete-deny"}
_VALID_UFW_PROTOS = {"tcp", "udp"}


def _argv_ufw_toggle(params):
    on = params.get("on")
    if not isinstance(on, bool):
        raise ValueError("ufw_toggle requires 'on' as bool")
    if on:
        return ["sudo", "-n", "ufw", "--force", "enable"]
    else:
        return ["sudo", "-n", "ufw", "disable"]


def _risk_ufw_toggle(params):
    on = params.get("on")
    return "risky" if on else "safe"


def _argv_ufw_rule(params):
    verb = params.get("verb", "")
    port = params.get("port")
    proto = params.get("proto", "")
    if verb not in _VALID_UFW_VERBS:
        raise ValueError(f"ufw_rule verb {verb!r} must be one of {sorted(_VALID_UFW_VERBS)}")
    if not isinstance(port, int) or not (1 <= port <= 65535):
        raise ValueError(f"ufw_rule port must be int 1-65535, got {port!r}")
    if proto not in _VALID_UFW_PROTOS:
        raise ValueError(f"ufw_rule proto {proto!r} must be one of {sorted(_VALID_UFW_PROTOS)}")
    portproto = f"{port}/{proto}"
    if verb == "allow":
        return ["sudo", "-n", "ufw", "allow", portproto]
    elif verb == "deny":
        return ["sudo", "-n", "ufw", "deny", portproto]
    elif verb == "delete-allow":
        return ["sudo", "-n", "ufw", "delete", "allow", portproto]
    elif verb == "delete-deny":
        return ["sudo", "-n", "ufw", "delete", "deny", portproto]


def _fn_ddns_timer_enable(params, db_path=None):
    """Enable baza-ddns.timer; write unit file first if missing.

    Returns (rc, out, err) so run_action's fn path can audit uniformly.
    """
    if not os.path.exists(_TIMER_PATH):
        rc, out, err = _run(
            ["sudo", "-n", "tee", _TIMER_PATH],
            timeout=10,
            input_text=_TIMER_UNIT,
        )
        if rc != 0:
            return rc, out, err

    rc, out, err = _run(["sudo", "-n", "systemctl", "daemon-reload"], timeout=20)
    if rc != 0:
        return rc, out, err

    rc, out, err = _run(
        ["sudo", "-n", "systemctl", "enable", "--now", "baza-ddns.timer"],
        timeout=20,
    )
    return rc, out, err


def _fn_ddns_timer_disable(params, db_path=None):
    """Disable baza-ddns.timer (disable --now).

    Returns (rc, out, err).
    """
    return _run(
        ["sudo", "-n", "systemctl", "disable", "--now", "baza-ddns.timer"],
        timeout=20,
    )


# ── ACTIONS table ─────────────────────────────────────────────────────────────
# Each entry: {"risk": str|None, "risk_fn": callable|absent, "desc": str,
#              "argv": callable(params)->list[str], "timeout": int|absent}
# risk_fn(params)->str overrides risk when verb-dependent.
# If an entry has a "fn" key (callable), run_action calls fn(params) instead of _run(argv).

ACTIONS = {
    "svc": {
        "risk": "safe",          # base; overridden per-call by risk_fn
        "risk_fn": _risk_svc,
        "desc": "Start/stop/restart a whitelisted systemd service",
        "argv": _argv_svc,
    },
    "ddns_run": {
        "risk": "safe",
        "desc": "Trigger a DDNS update (start baza-ddns.service)",
        "argv": _argv_ddns_run,
    },
    "ts_up": {
        "risk": "safe",
        "desc": "Bring Tailscale up",
        "argv": _argv_ts_up,
    },
    "ts_down": {
        "risk": "risky",
        "desc": "Bring Tailscale down (loses remote access)",
        "argv": _argv_ts_down,
    },
    "ts_exit_node": {
        "risk": "safe",
        "desc": "Advertise (or stop advertising) this node as a Tailscale exit node",
        "argv": _argv_ts_exit_node,
    },
    "ts_serve": {
        "risk": "safe",          # base; off=risky via risk_fn
        "risk_fn": _risk_ts_serve,
        "desc": "Enable/disable a Tailscale Serve mapping (dash or vision)",
        "argv": _argv_ts_serve,
    },
    "nic": {
        "risk": "safe",          # base; down=risky via risk_fn
        "risk_fn": _risk_nic,
        "desc": "Bring a network interface up or down",
        "argv": _argv_nic,
    },
    "dhcp_renew": {
        "risk": "risky",
        "desc": "Renew DHCP lease on an interface (may cause brief connectivity loss)",
        "argv": _argv_dhcp_renew,
        "timeout": 30,
    },
    "ddns_timer_enable": {
        "risk": "safe",
        "desc": "Create (if missing) and enable baza-ddns.timer for hourly WAN IP sync",
        "fn": _fn_ddns_timer_enable,
    },
    "ddns_timer_disable": {
        "risk": "risky",
        "desc": "Disable and stop baza-ddns.timer (DDNS will no longer auto-sync)",
        "fn": _fn_ddns_timer_disable,
    },
}


# ── Cloudflare migration wizard run-actions (fn-style, all audited) ───────────
# Registered here so they flow through run_action's audited fn path. The fns
# live in network_wizard (imported lazily to avoid an import cycle).

def _wiz_fn(name):
    """Return an fn(params, db_path=None) that dispatches to network_wizard.<name>."""
    def _dispatch(params, db_path=None):
        try:
            from dashboard import network_wizard
        except ImportError:
            import network_wizard
        return getattr(network_wizard, name)(params, db_path=db_path)
    return _dispatch


ACTIONS["ufw_toggle"] = {
    "risk": "safe",          # base; on=risky via risk_fn
    "risk_fn": _risk_ufw_toggle,
    "desc": "Enable (risky — may block tailscale/caddy if default-deny) or disable ufw",
    "argv": _argv_ufw_toggle,
}
ACTIONS["ufw_rule"] = {
    "risk": "safe",
    "desc": "Add or delete a ufw allow/deny rule (port/proto)",
    "argv": _argv_ufw_rule,
}

ACTIONS["wiz_tunnel_create"] = {
    "risk": "risky",
    "desc": "Create the Cloudflare tunnel (cloudflared tunnel create baza-dashboard)",
    "fn": _wiz_fn("wiz_tunnel_create"),
}
ACTIONS["wiz_write_config"] = {
    "risk": "risky",
    "desc": "Write ~/.cloudflared/config.yml (ingress baza.ahb123.com → localhost:8888)",
    "fn": _wiz_fn("wiz_write_config"),
}
ACTIONS["wiz_route_dns"] = {
    "risk": "risky",
    "desc": "Route DNS for baza.ahb123.com to the tunnel (creates proxied CNAME)",
    "fn": _wiz_fn("wiz_route_dns"),
}
ACTIONS["wiz_install_service"] = {
    "risk": "risky",
    "desc": "Install + enable the cloudflared systemd service (reboot-safe)",
    "fn": _wiz_fn("wiz_install_service"),
}


# ── public API ────────────────────────────────────────────────────────────────

def run_action(key: str, params: dict, db_path=None) -> dict:
    """Look up key → validate params → build argv → _run → audit → return dict.

    Validates action key and params; on rejection, audits the rejection (rc=-2)
    before raising ValueError (forensic trail for a dashboard with no auth).
    ALWAYS audits all outcomes (successes, execution failures, and rejections).
    """
    if key not in ACTIONS:
        network_db.audit(key or "<unknown>", params, -2, "", f"rejected: Unknown action key", db_path=db_path)
        raise ValueError(f"Unknown action key: {key!r}")

    entry = ACTIONS[key]

    # If a callable "fn" key exists, call it directly (future extension path)
    if "fn" in entry:
        fn = entry["fn"]
        try:
            rc, out, err = fn(params, db_path=db_path)
        except ValueError as e:
            network_db.audit(key, params, -2, "", f"rejected: {str(e)}", db_path=db_path)
            raise
        ok = rc == 0
        network_db.audit(key, params, rc, out, err, db_path=db_path)
        return {"ok": ok, "rc": rc, "out": out, "err": err, "action": key}

    # Build argv — validates params (raises ValueError on bad input)
    try:
        argv = entry["argv"](params)
    except ValueError as e:
        network_db.audit(key, params, -2, "", f"rejected: {str(e)}", db_path=db_path)
        raise

    timeout = entry.get("timeout", 20)
    rc, out, err = _run(argv, timeout=timeout)
    ok = rc == 0

    network_db.audit(key, params, rc, out, err, db_path=db_path)

    return {"ok": ok, "rc": rc, "out": out, "err": err, "action": key}


def action_meta() -> list:
    """Return list of {key, desc, risk} for every action in ACTIONS.

    risk is resolved without params (base risk string from the table).
    """
    out = []
    for key, entry in ACTIONS.items():
        out.append({
            "key": key,
            "desc": entry.get("desc", ""),
            "risk": entry.get("risk", "safe"),
        })
    return out


# ── Caddy editor pipeline ─────────────────────────────────────────────────────

_CADDY_LIVE    = "/etc/caddy/Caddyfile"
_CADDY_STAGED  = "/etc/caddy/.Caddyfile.staged"
_CADDY_BAK_DIR = "/etc/caddy"
_CADDY_BAK_RE  = re.compile(r"^Caddyfile\.bak\.[\w-]+$")


def caddy_read() -> dict:
    """Return the live Caddyfile text and list of backup names.

    Returns: {"path": str, "text": str, "backups": list[str]}
    """
    rc, text, _ = _run(["sudo", "-n", "cat", _CADDY_LIVE], timeout=10)
    if rc != 0:
        text = ""

    rc2, find_out, _ = _run(
        ["sudo", "-n", "find", _CADDY_BAK_DIR, "-maxdepth", "1", "-name", "Caddyfile.bak.*", "-printf", "%f\n"],
        timeout=10,
    )
    backups = []
    if rc2 == 0:
        for line in find_out.strip().splitlines():
            name = line.strip()
            if _CADDY_BAK_RE.fullmatch(name):
                backups.append(name)
    backups.sort(reverse=True)

    return {"path": _CADDY_LIVE, "text": text, "backups": backups}


def caddy_apply(text: str, db_path=None) -> dict:
    """Write text to staged, validate, backup live, replace live, reload caddy.

    Pipeline:
      1. tee text to /etc/caddy/.Caddyfile.staged (via sudo -n tee, stdin)
      2. caddy validate --config staged
         → on fail: audit + return {ok:False, stage:"validate", err:stderr}
      3. cp live → /etc/caddy/Caddyfile.bak.<timestamp>
      4. cp staged → live
      5. systemctl reload caddy
      6. audit + return {ok:True, stage:"done", err:""}

    Empty text is rejected immediately (no sudo calls).
    """
    if not text or not text.strip():
        network_db.audit("caddy_apply", {"len": 0}, -2, "", "rejected: empty text", db_path=db_path)
        return {"ok": False, "stage": "validate", "err": "empty text rejected"}

    # Step 1: write staged via tee
    rc, out, err = _run(
        ["sudo", "-n", "tee", _CADDY_STAGED],
        timeout=15,
        input_text=text,
    )
    if rc != 0:
        network_db.audit("caddy_apply", {"stage": "tee"}, rc, out, err, db_path=db_path)
        return {"ok": False, "stage": "tee", "err": err}

    # Step 2: validate staged config (Caddyfile format needs the caddyfile adapter)
    rc, out, err = _run(
        ["sudo", "-n", "caddy", "validate", "--adapter", "caddyfile", "--config", _CADDY_STAGED],
        timeout=20,
    )
    if rc != 0:
        network_db.audit("caddy_apply", {"stage": "validate"}, rc, out, err, db_path=db_path)
        return {"ok": False, "stage": "validate", "err": err or out}

    # Step 3: backup live Caddyfile
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak_path = f"{_CADDY_BAK_DIR}/Caddyfile.bak.{ts}"
    rc, out, err = _run(
        ["sudo", "-n", "cp", _CADDY_LIVE, bak_path],
        timeout=10,
    )
    if rc != 0:
        network_db.audit("caddy_apply", {"stage": "backup"}, rc, out, err, db_path=db_path)
        return {"ok": False, "stage": "backup", "err": err}

    # Step 4: replace live with staged
    rc, out, err = _run(
        ["sudo", "-n", "cp", _CADDY_STAGED, _CADDY_LIVE],
        timeout=10,
    )
    if rc != 0:
        network_db.audit("caddy_apply", {"stage": "cp_live"}, rc, out, err, db_path=db_path)
        return {"ok": False, "stage": "cp_live", "err": err}

    # Step 5: reload caddy
    rc, out, err = _run(
        ["sudo", "-n", "systemctl", "reload", "caddy"],
        timeout=20,
    )
    ok = rc == 0
    stage = "done" if ok else "reload"
    network_db.audit("caddy_apply", {"stage": stage, "bak": bak_path}, rc, out, err, db_path=db_path)
    return {"ok": ok, "stage": stage, "err": err if not ok else ""}


# ── Diagnostics toolbox (Task 12) ────────────────────────────────────────────
# run_diag is NOT an ACTIONS entry — routes call it directly.
# Strict input validation is the security boundary: target/url/count/port/rtype
# are all validated with re.fullmatch / range checks before argv is built.

_DIAG_TARGET_RE = re.compile(r"[A-Za-z0-9.\-]{1,253}")
_DIAG_URL_RE = re.compile(
    r"https?://[A-Za-z0-9.\-]+(?::\d+)?(/[A-Za-z0-9.\-_/?=&%]*)?"
)
_DIAG_VALID_RTYPES = {"A", "AAAA", "CNAME", "MX", "NS", "TXT", "SOA"}
_DIAG_VALID_TOOLS = {"ping", "traceroute", "dig", "curl", "port"}


def run_diag(tool: str, target: str, extra: dict, db_path=None) -> dict:
    """Run a network diagnostic tool with strict argument validation.

    tool    — one of: ping, traceroute, dig, curl, port
    target  — hostname/IPv4 for ping/traceroute/dig/port;
              http(s):// URL for curl (passed as 'target' by the caller)
    extra   — optional overrides: count (ping), rtype (dig), port (port tool)
    db_path — sqlite path for audit; defaults to network_db default

    Returns {"ok": bool, "out": str, "err": str, "rc": int}.
    Raises ValueError on any validation failure (routes map that to HTTP 400).
    All calls are audited as diag_<tool>.
    """
    if tool not in _DIAG_VALID_TOOLS:
        raise ValueError(f"tool {tool!r} not in whitelist {sorted(_DIAG_VALID_TOOLS)}")

    if tool == "curl":
        url = target
        if not _DIAG_URL_RE.fullmatch(url):
            raise ValueError(
                f"invalid url {url!r}; must match https?://hostname[/path]"
            )
        argv = ["curl", "-sSI", "--max-time", "8", url]
        audit_params = {"url": url}
        timeout = 20

    else:
        # All non-curl tools take a hostname/IPv4 target
        if not _DIAG_TARGET_RE.fullmatch(target):
            raise ValueError(
                f"invalid target {target!r}; must match [A-Za-z0-9.\\-]{{1,253}}"
            )

        if tool == "ping":
            count = extra.get("count", 4)
            if not isinstance(count, int) or not (1 <= count <= 10):
                raise ValueError(f"count must be int 1-10, got {count!r}")
            argv = ["ping", "-c", str(count), "-W", "2", target]
            audit_params = {"target": target, "count": count}
            timeout = 20

        elif tool == "traceroute":
            argv = ["traceroute", "-w", "2", "-m", "20", target]
            audit_params = {"target": target}
            timeout = 45

        elif tool == "dig":
            rtype = extra.get("rtype", "A")
            if rtype not in _DIAG_VALID_RTYPES:
                raise ValueError(
                    f"rtype {rtype!r} must be one of {sorted(_DIAG_VALID_RTYPES)}"
                )
            argv = ["dig", "+short", target, rtype]
            audit_params = {"target": target, "rtype": rtype}
            timeout = 20

        elif tool == "port":
            port = extra.get("port")
            if not isinstance(port, int) or not (1 <= port <= 65535):
                raise ValueError(f"port must be int 1-65535, got {port!r}")
            argv = ["nc", "-z", "-v", "-w", "3", target, str(port)]
            audit_params = {"target": target, "port": port}
            timeout = 20

    rc, out, err = _run(argv, timeout=timeout)
    ok = rc == 0
    network_db.audit(f"diag_{tool}", audit_params, rc, out, err, db_path=db_path)
    return {"ok": ok, "rc": rc, "out": out, "err": err}


def caddy_rollback(backup_name: str, db_path=None) -> dict:
    """Restore a backup Caddyfile to live using the same validate→bak→cp→reload pipeline.

    backup_name must match re.fullmatch(r"Caddyfile\\.bak\\.[\\w-]+", name).
    Raises ValueError for invalid / traversal names.
    """
    if not backup_name or not _CADDY_BAK_RE.fullmatch(backup_name):
        raise ValueError(
            f"caddy_rollback: invalid backup name {backup_name!r}; "
            f"must match Caddyfile.bak.[\\w-]+"
        )

    bak_path = f"{_CADDY_BAK_DIR}/{backup_name}"

    # Step 1: copy backup to staged
    rc, out, err = _run(
        ["sudo", "-n", "cp", bak_path, _CADDY_STAGED],
        timeout=10,
    )
    if rc != 0:
        network_db.audit("caddy_rollback", {"backup": backup_name, "stage": "cp_staged"}, rc, out, err, db_path=db_path)
        return {"ok": False, "stage": "cp_staged", "err": err}

    # Step 2: validate staged (Caddyfile format needs the caddyfile adapter)
    rc, out, err = _run(
        ["sudo", "-n", "caddy", "validate", "--adapter", "caddyfile", "--config", _CADDY_STAGED],
        timeout=20,
    )
    if rc != 0:
        network_db.audit("caddy_rollback", {"backup": backup_name, "stage": "validate"}, rc, out, err, db_path=db_path)
        return {"ok": False, "stage": "validate", "err": err or out}

    # Step 3: backup current live
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    cur_bak = f"{_CADDY_BAK_DIR}/Caddyfile.bak.{ts}"
    rc, out, err = _run(
        ["sudo", "-n", "cp", _CADDY_LIVE, cur_bak],
        timeout=10,
    )
    if rc != 0:
        network_db.audit("caddy_rollback", {"backup": backup_name, "stage": "backup"}, rc, out, err, db_path=db_path)
        return {"ok": False, "stage": "backup", "err": err}

    # Step 4: replace live with staged
    rc, out, err = _run(
        ["sudo", "-n", "cp", _CADDY_STAGED, _CADDY_LIVE],
        timeout=10,
    )
    if rc != 0:
        network_db.audit("caddy_rollback", {"backup": backup_name, "stage": "cp_live"}, rc, out, err, db_path=db_path)
        return {"ok": False, "stage": "cp_live", "err": err}

    # Step 5: reload caddy
    rc, out, err = _run(
        ["sudo", "-n", "systemctl", "reload", "caddy"],
        timeout=20,
    )
    ok = rc == 0
    stage = "done" if ok else "reload"
    network_db.audit(
        "caddy_rollback",
        {"backup": backup_name, "stage": stage, "cur_bak": cur_bak},
        rc, out, err,
        db_path=db_path,
    )
    return {"ok": ok, "stage": stage, "err": err if not ok else ""}
