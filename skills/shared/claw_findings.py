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

try:
    from core import claw_review_db
except ImportError as e:
    print(json.dumps({"error": f"claw_review_db unavailable: {e}"}))
    sys.exit(1)

if args.get("counts"):
    print(json.dumps({"severity_counts": claw_review_db.severity_counts()}))
    sys.exit(0)

rows = claw_review_db.recent(
    limit=int(args.get("limit", 20)),
    severity=args.get("severity") or None,
    status=args.get("status", "open"),
)
slim = [
    {k: r.get(k) for k in ("id", "created_at", "severity", "cadence", "target", "title", "detail", "labels")}
    for r in rows
]
print(json.dumps({"count": len(slim), "findings": slim}, default=str))
