#!/usr/bin/env python3
"""Quick network speed estimate using download test."""
import os, json, time, urllib.request

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
url = args.get("url", "http://speedtest.tele2.net/1MB.zip")
size_mb = float(args.get("size_mb", 1))

try:
    start = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": "BazaSpeedTest/1.0"})
    response = urllib.request.urlopen(req, timeout=30)
    data = response.read()
    elapsed = time.time() - start
    actual_mb = len(data) / (1024 * 1024)
    speed_mbps = round(actual_mb * 8 / elapsed, 2)
    print(json.dumps({
        "download_speed_mbps": speed_mbps,
        "downloaded_mb": round(actual_mb, 2),
        "time_seconds": round(elapsed, 2),
        "test_url": url
    }))
except Exception as e:
    print(json.dumps({"error": str(e)}))
