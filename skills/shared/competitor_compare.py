#!/usr/bin/env python3
"""Compare against industry benchmarks for home renovation."""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
benchmarks = {"avg_project_value": 15000, "profit_margin_pct": 25, "completion_rate_pct": 90, "avg_days_to_complete": 14, "client_retention_pct": 40, "avg_hourly_rate": 35}
our = args.get("metrics", {})
comparison = {}
for k, industry_avg in benchmarks.items():
    ours = our.get(k, 0)
    comparison[k] = {"industry_avg": industry_avg, "ours": ours, "diff_pct": round((ours - industry_avg) / industry_avg * 100, 1) if industry_avg else 0, "better": ours >= industry_avg}
print(json.dumps({"benchmarks": comparison}))
