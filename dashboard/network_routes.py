"""Flask blueprint for the Network tab — map + controls for the whole stack."""
from flask import Blueprint, jsonify, render_template, request

try:
    from dashboard import network_db, network_ops, network_probe, network_dns, network_wizard
except ImportError:
    import network_db, network_ops, network_probe, network_dns, network_wizard

network_bp = Blueprint("network", __name__)


_ROUTER_SEED = [
    ("router.model", "Fios G3100", ""),
    ("router.admin", "http://192.168.1.1", ""),
    ("router.reservation", "enp6s0 f0:2f:74:1b:aa:e9 → 192.168.1.68", ""),
    ("router.port_forward", "443,80 → 192.168.1.68", "verify: https://nova.ahb123.com/health"),
]


def init_network():
    network_db.ensure_tables()
    # Seed manual router facts only if the table is empty
    if not network_db.facts_list():
        for key, value, note in _ROUTER_SEED:
            network_db.fact_set(key, value, note)


@network_bp.route("/network")
def network_page():
    return render_template("network.html", nav_active="network")


@network_bp.route("/api/network/status")
def api_status():
    return jsonify(network_probe.status())


@network_bp.route("/api/network/action", methods=["POST"])
def api_action():
    body = request.get_json(force=True, silent=True) or {}
    body = body if isinstance(body, dict) else {}
    params = body.get("params")
    params = params if isinstance(params, dict) else {}
    try:
        return jsonify(network_ops.run_action(body.get("action", ""), params))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@network_bp.route("/api/network/audit")
def api_audit():
    try:
        limit = int(request.args.get("limit", 200))
    except ValueError:
        limit = 200
    limit = max(1, min(limit, 500))
    return jsonify({"rows": network_db.recent_audit(limit)})


@network_bp.route("/api/network/caddyfile", methods=["GET"])
def api_caddy_read():
    return jsonify(network_ops.caddy_read())


@network_bp.route("/api/network/caddyfile", methods=["POST"])
def api_caddy_apply():
    body = request.get_json(force=True, silent=True) or {}
    body = body if isinstance(body, dict) else {}
    text = body.get("text", "")
    if not text or not isinstance(text, str) or not text.strip():
        return jsonify({"error": "text is required and must be non-empty"}), 400
    result = network_ops.caddy_apply(text)
    return jsonify(result)


@network_bp.route("/api/network/caddyfile/rollback", methods=["POST"])
def api_caddy_rollback():
    body = request.get_json(force=True, silent=True) or {}
    body = body if isinstance(body, dict) else {}
    name = body.get("name", "")
    try:
        result = network_ops.caddy_rollback(name)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(result)


# ── deSEC DNS provider token routes ────────────────────────────────────────

@network_bp.route("/api/network/token/<provider>", methods=["GET"])
def api_token_get(provider):
    """Return {"set": bool} — NEVER the token value itself."""
    tok = network_db.get_token(provider)
    return jsonify({"set": tok is not None})


@network_bp.route("/api/network/token/<provider>", methods=["POST"])
def api_token_set(provider):
    """Accept {token} body, store it, return {"ok": true}."""
    body = request.get_json(force=True, silent=True) or {}
    body = body if isinstance(body, dict) else {}
    token = body.get("token", "")
    if not token or not isinstance(token, str) or not token.strip():
        return jsonify({"error": "token is required and must be a non-empty string"}), 400
    network_db.set_token(provider, token.strip())
    return jsonify({"ok": True})


# ── Cloudflare zone status route ───────────────────────────────────────────

@network_bp.route("/api/network/cloudflare", methods=["GET"])
def api_cloudflare_status():
    """Return cf_zone_status for ahb123.com. 400 if no token stored."""
    tok = network_db.get_token("cloudflare")
    if not tok:
        return jsonify({"error": "no token — paste a Cloudflare token first"}), 400
    result = network_dns.cf_zone_status(tok)
    return jsonify(result)


# ── Cloudflare migration wizard routes ─────────────────────────────────────

@network_bp.route("/api/network/wizard", methods=["GET"])
def api_wizard():
    """Return the resolved phase list (live probe evidence merged over stored state)."""
    status = network_probe.status()
    wizard_db = network_db.wizard_get()
    return jsonify({"phases": network_wizard.detect(status, wizard_db)})


@network_bp.route("/api/network/wizard/mark", methods=["POST"])
def api_wizard_mark():
    """Set a manual phase's state (Serge's 'Mark done'). Body: {phase, state, note?}."""
    body = request.get_json(force=True, silent=True) or {}
    body = body if isinstance(body, dict) else {}
    phase = body.get("phase", "")
    state = body.get("state", "")
    note = body.get("note", "")
    valid_phases = {p["id"] for p in network_wizard.PHASES}
    valid_states = {"todo", "done", "verified", "blocked"}
    if phase not in valid_phases:
        return jsonify({"error": f"unknown phase {phase!r}"}), 400
    if state not in valid_states:
        return jsonify({"error": f"state must be one of {sorted(valid_states)}"}), 400
    note = note if isinstance(note, str) else ""
    network_db.wizard_set(phase, state, note)
    return jsonify({"ok": True, "phase": phase, "state": state})


@network_bp.route("/api/network/wizard/verify", methods=["POST"])
def api_wizard_verify():
    """Run a phase's verify fn. Body: {phase}. Returns {ok, evidence}."""
    body = request.get_json(force=True, silent=True) or {}
    body = body if isinstance(body, dict) else {}
    phase = body.get("phase", "")
    valid_phases = {p["id"] for p in network_wizard.PHASES}
    if phase not in valid_phases:
        return jsonify({"error": f"unknown phase {phase!r}"}), 400
    return jsonify(network_wizard.run_verify(phase))


# ── deSEC RRset routes ──────────────────────────────────────────────────────

@network_bp.route("/api/network/desec", methods=["GET"])
def api_desec_get():
    """Return list of RRsets for nova.ahb123.com.  400 if no token stored."""
    tok = network_db.get_token("desec")
    if not tok:
        return jsonify({"error": "no token — paste a deSEC token first"}), 400
    rrsets = network_dns.desec_rrsets(tok)
    return jsonify(rrsets)


@network_bp.route("/api/network/diag", methods=["POST"])
def api_diag():
    """Run a network diagnostic tool.

    Body: {tool, target, extra?}
    Returns run_diag result or 400 on validation error.
    """
    body = request.get_json(force=True, silent=True) or {}
    body = body if isinstance(body, dict) else {}
    tool = body.get("tool", "")
    target = body.get("target", "")
    extra = body.get("extra") or {}
    extra = extra if isinstance(extra, dict) else {}
    try:
        result = network_ops.run_diag(tool, target, extra)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(result)


@network_bp.route("/api/network/registry", methods=["GET"])
def api_registry():
    """Return settings_registry(status(), facts_list()) as {rows: [...]}."""
    import os, re, glob as _glob
    s = network_probe.status()
    facts = network_db.facts_list()
    # Collect relevant env vars from repo-root .env* files
    env = {}
    _repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _relevant = re.compile(r'^(BAZA_|NOVA_|CADDY_|SECRET|API_KEY|TOKEN)', re.IGNORECASE)
    for env_file in sorted(_glob.glob(os.path.join(_repo_root, ".env*"))):
        try:
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    k = k.strip()
                    if _relevant.match(k):
                        env[k] = v.strip()
        except Exception:
            pass
    rows = network_probe.settings_registry(s, facts, env or None)
    return jsonify({"rows": rows})


@network_bp.route("/api/network/facts", methods=["POST"])
def api_facts_set():
    """Create or update a manual_fact. Body: {key, value, note?}."""
    body = request.get_json(force=True, silent=True) or {}
    body = body if isinstance(body, dict) else {}
    key = body.get("key", "")
    value = body.get("value")
    note = body.get("note", "")
    if not key or not isinstance(key, str) or not key.strip():
        return jsonify({"error": "key is required"}), 400
    if value is None or not isinstance(value, str):
        return jsonify({"error": "value is required and must be a string"}), 400
    note = note if isinstance(note, str) else ""
    network_db.fact_set(key.strip(), value, note)
    return jsonify({"ok": True})


@network_bp.route("/api/network/facts/<path:key>", methods=["DELETE"])
def api_facts_delete(key):
    """Delete a manual_fact by key."""
    network_db.fact_delete(key)
    return jsonify({"ok": True})


@network_bp.route("/api/network/desec", methods=["POST"])
def api_desec_set():
    """Create or replace a single RRset.

    Body: {subname, rtype, ttl, records[]}
    Audit row written with action='desec_set'; token never included in params.
    """
    tok = network_db.get_token("desec")
    if not tok:
        return jsonify({"error": "no token — paste a deSEC token first"}), 400
    body = request.get_json(force=True, silent=True) or {}
    body = body if isinstance(body, dict) else {}
    subname = body.get("subname", "@")
    rtype = body.get("rtype", "")
    ttl = body.get("ttl", 300)
    records = body.get("records", [])
    # Audit params: no token
    audit_params = {"subname": subname, "rtype": rtype, "ttl": ttl, "records": records}
    try:
        result = network_dns.desec_set_rrset(tok, subname, rtype, ttl, records)
        network_db.audit("desec_set", audit_params, 0, str(result), "")
        return jsonify(result)
    except ValueError as e:
        network_db.audit("desec_set", audit_params, 1, "", str(e))
        return jsonify({"error": str(e)}), 400
