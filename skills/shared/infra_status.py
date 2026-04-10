#!/usr/bin/env python3
"""Get full infrastructure status from the dashboard API."""
import os, json, urllib.request

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
base = args.get("base_url", "http://localhost:8888")

try:
    req = urllib.request.urlopen(f"{base}/api/infra/metrics", timeout=10)
    data = json.loads(req.read().decode())

    summary = {
        "hostname": data.get("hostname"),
        "uptime": data.get("uptime"),
        "cpu": f"{data.get('cpu_model','')} ({data.get('cpu_cores','')} cores) @ {data.get('cpu_temp','')}",
        "memory": data.get("mem_usage"),
        "disk": data.get("disk_usage"),
        "total_storage_tb": data.get("total_storage_tb"),
        "gpus": [{"name":g["name"],"temp":g.get("temp"),"vram":f"{g.get('memory_used',0)}/{g.get('memory_total',0)} MB"} for g in data.get("gpus",[])],
        "services": data.get("services", {}),
        "agents": data.get("agents", {}),
        "api_routes": data.get("api_routes"),
        "db_stats": data.get("db_stats", {}),
    }
    print(json.dumps(summary))
except Exception as e:
    print(json.dumps({"error": str(e)}))
