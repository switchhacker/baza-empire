"""Flask Blueprint for read-only Claw review findings (/api/claw/*).

Thin HTTP layer over core.claw_review_db so remote hosts (phantom/Specter)
can read findings without a copy of claw_reviews.db.
"""
import os
import sys

from flask import Blueprint, request, jsonify

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core import claw_review_db  # noqa: E402

claw_bp = Blueprint("claw_api", __name__)

_ALLOWED_SEVERITIES = {"info", "warn", "bug", "regression", "security"}


@claw_bp.route("/api/claw/findings")
def claw_findings():
    if request.args.get("counts"):
        return jsonify({"severity_counts": claw_review_db.severity_counts()})
    severity = (request.args.get("severity") or "").strip() or None
    if severity and severity not in _ALLOWED_SEVERITIES:
        return jsonify({"error": f"severity must be one of {sorted(_ALLOWED_SEVERITIES)}"}), 400
    try:
        limit = max(1, min(int(request.args.get("limit", 20)), 200))
    except (TypeError, ValueError):
        limit = 20
    rows = claw_review_db.recent(
        limit=limit,
        severity=severity,
        status=(request.args.get("status") or "open").strip() or "open",
    )
    slim = [
        {
            "id": r.get("id"),
            "ts": r.get("ts"),
            "severity": r.get("severity"),
            "cadence": r.get("cadence"),
            "target": r.get("target"),
            "title": r.get("title"),
            "body": (r.get("body") or "")[:500],
            "labels": r.get("labels", []),
        }
        for r in rows
    ]
    return jsonify({"count": len(slim), "findings": slim})
