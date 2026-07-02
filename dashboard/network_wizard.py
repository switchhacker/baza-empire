"""network_wizard.py — Cloudflare-Tunnel migration wizard for the Network tab.

Mirrors ~/Desktop/ahb123-cloudflare-tunnel-plan.md: an 8-phase (phase0..phase8)
checklist that links the baza dashboard to baza.ahb123.com via a Cloudflare
Tunnel + Access gate.

Design contract:
  - detect(status, wizard_db) is PURE — it takes the already-collected probe
    status dict and the stored wizard_state dict, merges live evidence over
    stored "Mark done" state, and returns a resolved list. It NEVER shells out.
  - verify_* helpers may shell out (via network_probe._run / probe_dns) — they
    are read-only DNS checks.
  - Run-action fns (wiz_*) are the only code paths that mutate the system, and
    they are ONLY reached through network_ops.run_action (audited, explicit).
    Nothing here creates a tunnel, writes config, or flips DNS on import.

The migration is blocked on Serge's Phase 1 (Cloudflare account); until he
authorizes/logs in, the run-action fns fail gracefully (rc != 0), which
run_action audits and the UI surfaces as a toast — never a 500.
"""
import os
import re

def _probe():
    """Resolve the network_probe module (dual-path). Prefer an already-imported
    top-level `network_probe` so tests that patch it are honored."""
    import sys
    if "network_probe" in sys.modules:
        return sys.modules["network_probe"]
    try:
        from dashboard import network_probe as m
    except ImportError:  # pragma: no cover
        import network_probe as m
    return m


def _ops():
    """Lazily resolve the network_ops module (dual-path, avoids import cycle).

    Returns the SAME module object the caller imported, so a test that does
    `import network_ops; monkeypatch.setattr(network_ops, "_run", ...)` is honored
    by the run-action fns below.
    """
    import sys
    if "network_ops" in sys.modules:
        return sys.modules["network_ops"]
    try:
        from dashboard import network_ops as m
    except ImportError:  # pragma: no cover
        import network_ops as m
    return m


# UUID v4-ish matcher (8-4-4-4-12 hex) — robust to surrounding whitespace/columns.
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)

TUNNEL_NAME = "baza-dashboard"
HOSTNAME = "baza.ahb123.com"
LOCAL_SERVICE = "http://localhost:8888"


# ─── PHASES (mirrors the plan file exactly) ──────────────────────────────────

PHASES = [
    {
        "id": "phase0",
        "who": "claude",
        "title": "Prep",
        "instructions": (
            "**Phase 0 — Prep 🟢 (DONE when cloudflared is installed)**\n\n"
            "- cloudflared 2026.6.1 installed on baza.\n"
            "- Authoritative DNS audit captured → record sheet written "
            "(`ahb123-cloudflare-migration.md`)."
        ),
        "run": None,
        "verify": None,
    },
    {
        "id": "phase1",
        "who": "serge",
        "title": "Cloudflare account + add zone",
        "instructions": (
            "**Phase 1 — Cloudflare account + add zone 🔴 (Serge, in browser)**\n\n"
            "1. Log in / create a **free** Cloudflare account.\n"
            "2. Add site → `ahb123.com` (Free plan). Let it scan.\n\n"
            "_Everything is blocked on this step. Click **Mark done** once the zone "
            "is added and Cloudflare has finished its scan._"
        ),
        "run": None,
        "verify": None,
    },
    {
        "id": "phase2",
        "who": "serge",
        "title": "DNS safety gate (BEFORE nameserver flip)",
        "instructions": (
            "**Phase 2 — DNS safety gate (BEFORE the nameserver flip) 🔴 (Claude guides)**\n\n"
            "Reconcile Cloudflare's scanned records against the record sheet. "
            "Must-fix items Cloudflare's scan gets wrong:\n\n"
            "- **4 apex A records + the `www` CNAME → set to DNS only (grey cloud).** "
            "Proxying (orange cloud) breaks Squarespace SSL.\n"
            "- **`nova` NS records** (`ns1.desec.io` / `ns2.desec.org`) → the scan "
            "usually **SKIPS** these; add them manually or nova dies.\n"
            "- **MX** `smtp.google.com` (priority 1), **SPF TXT**, and **DKIM TXT** → "
            "confirm all present, or email breaks.\n\n"
            "Use **Verify** below to re-check MX + SPF + DKIM against live DNS before "
            "you flip nameservers."
        ),
        "run": None,
        "verify": "verify_email",
    },
    {
        "id": "phase3",
        "who": "serge",
        "title": "Nameserver change (reversible)",
        "instructions": (
            "**Phase 3 — Nameserver change 🔴 (reversible)**\n\n"
            "- At the registrar, swap `ns-cloud-e*.googledomains.com` → Cloudflare's "
            "2 nameservers.\n"
            "- **Rollback = revert the nameservers**; the Google Cloud DNS zone stays "
            "intact.\n\n"
            "Auto-detects done once `dig NS ahb123.com` returns Cloudflare nameservers."
        ),
        "run": None,
        "verify": "verify_ns",
    },
    {
        "id": "phase4",
        "who": "claude",
        "title": "Activation",
        "instructions": (
            "**Phase 4 — Activation ⏳**\n\n"
            "- Wait for the Cloudflare \"active\" email.\n"
            "- Claude polls `dig NS ahb123.com` — auto-done when both nameservers "
            "end in `.ns.cloudflare.com`."
        ),
        "run": None,
        "verify": "verify_ns",
    },
    {
        "id": "phase5",
        "who": "claude",
        "title": "Create tunnel",
        "instructions": (
            "**Phase 5 — Create tunnel 🔴 login / 🟢 rest**\n\n"
            "- 🔴 **Serge:** run `cloudflared tunnel login` (browser authorize, pick "
            "`ahb123.com`). This can't be done from the dashboard.\n"
            "- 🟢 **Create tunnel** → `cloudflared tunnel create baza-dashboard`.\n"
            "- 🟢 **Write config** → `~/.cloudflared/config.yml` "
            "(ingress `baza.ahb123.com` → `http://localhost:8888`).\n"
            "- 🟢 **Route DNS** → `cloudflared tunnel route dns baza-dashboard "
            "baza.ahb123.com` (creates the proxied CNAME).\n\n"
            "_Buttons will fail gracefully until Serge has run the login above._\n\n"
            "Auto-done once `~/.cloudflared/config.yml` exists."
        ),
        "run": None,   # this phase has three sub-actions, rendered separately in UI
        "verify": None,
    },
    {
        "id": "phase6",
        "who": "claude",
        "title": "Service",
        "instructions": (
            "**Phase 6 — Service 🟢**\n\n"
            "- Install the cloudflared systemd unit (auto-start, reboot-safe).\n\n"
            "Auto-done once `cloudflared.service` is active."
        ),
        "run": "wiz_install_service",
        "verify": None,
    },
    {
        "id": "phase7",
        "who": "serge",
        "title": "Cloudflare Access gate",
        "instructions": (
            "**Phase 7 — Cloudflare Access gate 🔴 (Claude guides)**\n\n"
            "- Zero Trust → Access → **Add application (self-hosted)** → "
            "`baza.ahb123.com`.\n"
            "- Policy: **allow emails = `contactahbco@gmail.com`** (Google or "
            "email-OTP).\n\n"
            "_The dashboard has NO built-in auth (family mode, user_id=1) → this gate "
            "is required, not optional._ Click **Mark done** once the Access app + "
            "policy are live."
        ),
        "run": None,
        "verify": None,
    },
    {
        "id": "phase8",
        "who": "claude",
        "title": "Verify",
        "instructions": (
            "**Phase 8 — Verify 🟢**\n\n"
            "- `https://baza.ahb123.com` → Access login → dashboard.\n"
            "- Re-check: `ahb123.com`, `www`, `nova`, and email all still "
            "resolve/work.\n\n"
            "Auto-done once `https://baza.ahb123.com` responds OK."
        ),
        "run": None,
        "verify": "verify_baza_dns",
    },
]


# ─── verify helpers (read-only DNS; may shell out) ───────────────────────────

def verify_ns():
    """dig NS ahb123.com → {ok, actual}. ok when both NS end in .ns.cloudflare.com."""
    try:
        rc, out, _ = _probe()._run(["dig", "+short", "NS", "ahb123.com"], timeout=8)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "actual": [], "err": str(e)}
    actual = [ln.strip().rstrip(".").lower() for ln in (out or "").splitlines() if ln.strip()]
    cf = [a for a in actual if a.endswith(".ns.cloudflare.com")]
    ok = len(actual) >= 2 and len(cf) == len(actual)
    return {"ok": ok, "actual": actual}


def verify_baza_dns():
    """dig baza.ahb123.com → {ok, actual}. ok when it resolves to anything."""
    try:
        rc, out, _ = _probe()._run(["dig", "+short", "baza.ahb123.com"], timeout=8)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "actual": [], "err": str(e)}
    actual = [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
    return {"ok": bool(actual), "actual": actual}


def verify_email():
    """Reuse probe_dns() verdicts: MX + SPF + DKIM must all be present.

    Returns {ok, mx, spf, dkim, evidence}. Email safety is the phase-2 gate.
    """
    try:
        dns = _probe().probe_dns()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "mx": False, "spf": False, "dkim": False, "err": str(e)}

    def _find(name, rtype):
        for v in dns:
            if v.get("name") == name and v.get("rtype") == rtype:
                return v
        return None

    mx_v = _find("ahb123.com", "MX")
    txt_v = _find("ahb123.com", "TXT")
    dkim_v = _find("google._domainkey.ahb123.com", "TXT")

    mx = bool(mx_v and any("smtp.google.com" in a for a in (mx_v.get("actual") or [])))
    spf = bool(txt_v and any("spf1" in a.lower() for a in (txt_v.get("actual") or [])))
    dkim = bool(dkim_v and any("dkim1" in a.lower() for a in (dkim_v.get("actual") or [])))

    return {
        "ok": mx and spf and dkim,
        "mx": mx, "spf": spf, "dkim": dkim,
        "evidence": {
            "mx": (mx_v or {}).get("actual", []),
            "spf": (txt_v or {}).get("actual", []),
            "dkim_present": dkim,
        },
    }


_VERIFY_FNS = {
    "verify_ns": verify_ns,
    "verify_baza_dns": verify_baza_dns,
    "verify_email": verify_email,
}


def run_verify(phase_id):
    """Look up a phase's verify fn by phase id and run it. Returns {ok, evidence}."""
    phase = next((p for p in PHASES if p["id"] == phase_id), None)
    if not phase or not phase.get("verify"):
        return {"ok": None, "evidence": {"err": "phase has no verify step"}}
    fn = _VERIFY_FNS.get(phase["verify"])
    if not fn:
        return {"ok": None, "evidence": {"err": f"unknown verify fn {phase['verify']!r}"}}
    result = fn()
    return {"ok": result.get("ok"), "evidence": result}


# ─── detect() — PURE state resolution ────────────────────────────────────────

def _ns_is_cloudflare(dns_verdicts):
    """True when the ahb123.com NS verdict shows ≥2 Cloudflare nameservers."""
    for v in dns_verdicts or []:
        if v.get("name") == "ahb123.com" and v.get("rtype") == "NS":
            actual = [a.strip().rstrip(".").lower() for a in (v.get("actual") or []) if a]
            cf = [a for a in actual if a.endswith(".ns.cloudflare.com")]
            return len(actual) >= 2 and len(cf) == len(actual)
    return False


def _baza_reachable(reach):
    for r in reach or []:
        if r.get("url") == "https://baza.ahb123.com":
            return bool(r.get("ok"))
    return False


def detect(status, wizard_db):
    """PURE: merge live probe evidence over stored wizard state.

    status  — dict from network_probe.status() (or a fixture); reads
              status['cloudflared'], status['dns'], status['reach'].
    wizard_db — dict {phase_id: {state, note, ...}} from network_db.wizard_get().

    Returns a list of phase dicts (copies of PHASES entries) each augmented with
    resolved {state: todo|done|verified|blocked, evidence: str}.

    Rules:
      phase0 done if cloudflared installed
      phase3 done if dig NS shows cloudflare NS (nameserver flip took effect)
      phase4 done if dig NS shows cloudflare NS (activation)
      phase5 done if config_exists
      phase6 done if cloudflared unit active
      phase8 done if reach baza.ahb123.com ok
      manual phases (1,2,3,7) fall back to stored "Mark done" state; verify
      evidence upgrades them where available.
    """
    cf = (status or {}).get("cloudflared", {}) or {}
    dns = (status or {}).get("dns", []) or []
    reach = (status or {}).get("reach", []) or []
    wizard_db = wizard_db or {}

    installed = bool(cf.get("installed"))
    config_exists = bool(cf.get("config_exists"))
    unit_active = cf.get("unit_state") == "active"
    ns_cloudflare = _ns_is_cloudflare(dns)
    baza_ok = _baza_reachable(reach)

    # live evidence per phase id: (auto_done_bool, evidence_str) or None if manual-only
    evidence = {
        "phase0": (installed, "cloudflared installed" if installed else "cloudflared NOT installed"),
        "phase3": (ns_cloudflare, "dig NS → cloudflare" if ns_cloudflare else "NS not yet cloudflare"),
        "phase4": (ns_cloudflare, "zone active (cloudflare NS live)" if ns_cloudflare else "not yet active"),
        "phase5": (config_exists, "~/.cloudflared/config.yml present" if config_exists else "config not written"),
        "phase6": (unit_active, "cloudflared.service active" if unit_active else "service not active"),
        "phase8": (baza_ok, "https://baza.ahb123.com OK" if baza_ok else "baza.ahb123.com not reachable"),
    }

    resolved = []
    for phase in PHASES:
        pid = phase["id"]
        out = dict(phase)  # shallow copy of the static definition
        stored = wizard_db.get(pid, {}) or {}
        stored_state = stored.get("state")

        ev = evidence.get(pid)
        if ev is not None:
            auto_done, ev_str = ev
            if auto_done:
                # live evidence wins for auto-detected phases
                state = "verified" if pid in ("phase4", "phase8") else "done"
                out["state"] = state
                out["evidence"] = ev_str
            else:
                # no live evidence — fall back to stored "Mark done" (manual override)
                out["state"] = stored_state or "todo"
                out["evidence"] = ev_str
        else:
            # purely manual phase (1, 2, 7) — comes from stored state only
            out["state"] = stored_state or "todo"
            out["evidence"] = stored.get("note", "") or "awaiting Serge"

        resolved.append(out)

    return resolved


# ─── run-action fns (mutating; ONLY reached via network_ops.run_action) ──────
# Each returns (rc, out, err) so run_action's fn path audits uniformly.

def wiz_tunnel_create(params, db_path=None):
    """`cloudflared tunnel create baza-dashboard`.

    cloudflared auth lives in switchhacker's ~/.cloudflared and the dashboard
    runs as switchhacker, so no sudo needed. Fails gracefully (rc != 0) until
    Serge has run `cloudflared tunnel login`.
    """
    return _ops()._run(["cloudflared", "tunnel", "create", TUNNEL_NAME], timeout=30)


def wiz_write_config(params, db_path=None, home=None):
    """Discover the tunnel UUID from `cloudflared tunnel list` and write
    ~/.cloudflared/config.yml (plain open() into switchhacker's home — NO sudo).

    `home` is injectable for tests; defaults to the real ~ .
    Returns (rc, out, err).
    """
    # Discover UUID from the tunnel list (read-only).
    try:
        rc, out, err = _probe()._run(["cloudflared", "tunnel", "list"], timeout=10)
    except Exception as e:  # noqa: BLE001
        return -1, "", f"cloudflared tunnel list failed: {e}"
    if rc != 0:
        return rc, out, err or "cloudflared tunnel list failed (not authenticated?)"

    # Find the line whose fields include the tunnel name, then extract UUID from it.
    uuid = None
    for line in (out or "").splitlines():
        if TUNNEL_NAME in line:
            m = _UUID_RE.search(line)
            if m:
                uuid = m.group(0)
                break
    if uuid is None:
        return (
            -1, out,
            f"baza-dashboard tunnel not found in `cloudflared tunnel list` — create it first"
        )

    home_dir = home or os.path.expanduser("~")
    cf_dir = os.path.join(home_dir, ".cloudflared")
    try:
        os.makedirs(cf_dir, exist_ok=True)
    except Exception as e:  # noqa: BLE001
        return -1, "", f"could not create {cf_dir}: {e}"

    creds = os.path.join(cf_dir, f"{uuid}.json")
    config_yml = (
        f"tunnel: {TUNNEL_NAME}\n"
        f"credentials-file: {creds}\n"
        f"ingress:\n"
        f"  - hostname: {HOSTNAME}\n"
        f"    service: {LOCAL_SERVICE}\n"
        f"  - service: http_status:404\n"
    )
    cfg_path = os.path.join(cf_dir, "config.yml")
    try:
        with open(cfg_path, "w") as f:
            f.write(config_yml)
    except Exception as e:  # noqa: BLE001
        return -1, "", f"could not write {cfg_path}: {e}"

    return 0, f"wrote {cfg_path} (tunnel {uuid})", ""


def wiz_route_dns(params, db_path=None):
    """`cloudflared tunnel route dns baza-dashboard baza.ahb123.com` — creates the proxied CNAME."""
    return _ops()._run(
        ["cloudflared", "tunnel", "route", "dns", TUNNEL_NAME, HOSTNAME],
        timeout=30,
    )


def wiz_install_service(params, db_path=None):
    """Install + enable the cloudflared systemd unit (reboot-safe).

    `sudo -n cloudflared --config <config> service install`
    then `sudo -n systemctl enable --now cloudflared`.
    """
    ops = _ops()
    rc, out, err = ops._run(
        ["sudo", "-n", "cloudflared", "--config",
         "/home/switchhacker/.cloudflared/config.yml", "service", "install"],
        timeout=30,
    )
    if rc != 0:
        return rc, out, err
    rc2, out2, err2 = ops._run(
        ["sudo", "-n", "systemctl", "enable", "--now", "cloudflared"],
        timeout=30,
    )
    return rc2, (out + out2), (err + err2)
