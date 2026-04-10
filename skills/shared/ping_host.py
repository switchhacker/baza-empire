#!/usr/bin/env python3
"""Ping a host and return latency."""
import os, json, subprocess

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
host = args.get("host", "8.8.8.8")
count = int(args.get("count", 4))

try:
    result = subprocess.run(["ping", "-c", str(count), "-W", "3", host],
                          capture_output=True, text=True, timeout=30)
    lines = result.stdout.strip().split("\n")
    stats_line = [l for l in lines if "rtt" in l or "round-trip" in l]
    loss_line = [l for l in lines if "packet loss" in l]
    latency = {}
    if stats_line:
        parts = stats_line[0].split("=")[1].strip().split("/")
        latency = {"min_ms": float(parts[0]), "avg_ms": float(parts[1]),
                    "max_ms": float(parts[2])}
    loss = "unknown"
    if loss_line:
        for part in loss_line[0].split(","):
            if "loss" in part:
                loss = part.strip()
    print(json.dumps({"host": host, "reachable": result.returncode == 0,
                       "latency": latency, "packet_loss": loss}))
except Exception as e:
    print(json.dumps({"host": host, "reachable": False, "error": str(e)}))
