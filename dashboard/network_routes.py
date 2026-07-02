"""Flask blueprint for the Network tab — map + controls for the whole stack."""
from flask import Blueprint, jsonify, render_template, request

try:
    from dashboard import network_db, network_ops, network_probe, network_dns
except ImportError:
    import network_db, network_ops, network_probe, network_dns

network_bp = Blueprint("network", __name__)


def init_network():
    network_db.ensure_tables()


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


# ── deSEC RRset routes ──────────────────────────────────────────────────────

@network_bp.route("/api/network/desec", methods=["GET"])
def api_desec_get():
    """Return list of RRsets for nova.ahb123.com.  400 if no token stored."""
    tok = network_db.get_token("desec")
    if not tok:
        return jsonify({"error": "no token — paste a deSEC token first"}), 400
    rrsets = network_dns.desec_rrsets(tok)
    return jsonify(rrsets)


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
