#!/usr/bin/env python3
"""Query Claw's continuous code-review findings (claw_reviews.db)."""
import json
import os
import sys

SKILL_META = {
    "category": "infrastructure",
    "summary": "Read Claw Batto's continuous-review findings: open bugs, warnings, security issues found in the framework code and services.",
    "when_to_use": "when asked about code review findings, open bugs, security warnings, or what Claw's background reviewer has flagged",
    "args": {
        "severity": "optional filter: info|warn|bug|regression|security",
        "status": "row status, default 'open'",
        "limit": "max rows, default 20",
        "counts": "true → return severity counts summary instead of rows",
    },
}

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, BASE_DIR)

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))


def _remote_fallback():
    """claw_reviews.db lives on baza; on other hosts (phantom) read it over
    the dashboard API instead."""
    import urllib.parse
    import urllib.request
    base = os.environ.get("BAZA_DASHBOARD_URL", "http://localhost:8888")
    params = {}
    if args.get("counts"):
        params["counts"] = "1"
    else:
        if args.get("severity"):
            params["severity"] = args["severity"]
        params["status"] = args.get("status", "open")
        params["limit"] = int(args.get("limit", 20))
    url = base + "/api/claw/findings?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            print(r.read().decode())
    except Exception as e:
        print(json.dumps({"error": f"claw findings unavailable locally and dashboard unreachable: {e}"}))
        sys.exit(1)


# Local read needs both the module and the DB file (baza only); anything
# else — e.g. phantom's bootstrapped tree — goes through the dashboard API.
try:
    from core import claw_review_db
    _local_ok = claw_review_db.DB_PATH.exists()
except ImportError:
    _local_ok = False

if not _local_ok:
    _remote_fallback()
    sys.exit(0)

if args.get("counts"):
    print(json.dumps({"severity_counts": claw_review_db.severity_counts()}))
    sys.exit(0)

rows = claw_review_db.recent(
    limit=int(args.get("limit", 20)),
    severity=args.get("severity") or None,
    status=args.get("status", "open"),
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
print(json.dumps({"count": len(slim), "findings": slim}, default=str))
