#!/usr/bin/env python3
"""Verify insurance/COI status for a project."""
import os, json
from datetime import datetime

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
policy_number = args.get("policy_number", "AHB-GL-2026")
expiry_date = args.get("expiry_date", "2026-12-31")
coverage_type = args.get("type", "general_liability")

POLICIES = {
    "general_liability": {"min_coverage": 1000000, "description": "General Liability"},
    "workers_comp": {"min_coverage": 500000, "description": "Workers Compensation"},
    "auto": {"min_coverage": 300000, "description": "Commercial Auto"},
    "umbrella": {"min_coverage": 2000000, "description": "Umbrella Policy"},
}

policy = POLICIES.get(coverage_type, POLICIES["general_liability"])
try:
    exp = datetime.strptime(expiry_date, "%Y-%m-%d")
    days_remaining = (exp - datetime.now()).days
    status = "active" if days_remaining > 0 else "expired"
    urgent = days_remaining < 30 and days_remaining > 0
except ValueError:
    days_remaining = -1
    status = "unknown"
    urgent = True

print(json.dumps({
    "policy_number": policy_number,
    "coverage_type": policy["description"],
    "min_coverage_required": policy["min_coverage"],
    "expiry_date": expiry_date,
    "days_remaining": days_remaining,
    "status": status,
    "renewal_urgent": urgent,
    "coi_available": status == "active"
}))
