#!/usr/bin/env python3
"""
Baza Empire Skill — mining_status
Queries XMRig API for live mining stats. Falls back to process/log check.
Usage: ##SKILL:mining_status{}##
"""
import os, json, urllib.request, subprocess, re

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
XMRIG_API = args.get("api_url", "http://localhost:4067/2/summary")

lines = []
lines.append("⛏️ MINING STATUS")
lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")

def run(cmd):
    try:
        return subprocess.check_output(cmd, shell=True, text=True, timeout=5).strip()
    except Exception:
        return ""

def fetch_xmrig(url):
    try:
        req = urllib.request.Request(url, headers={"Authorization": "Bearer "})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())
    except Exception:
        return None

# Try XMRig API
data = fetch_xmrig(XMRIG_API)

if data:
    # Hashrate
    hr = data.get("hashrate", {})
    hr_10s = hr.get("total", [0])[0] if hr.get("total") else 0
    hr_60s = hr.get("total", [0, 0])[1] if len(hr.get("total", [])) > 1 else 0
    hr_15m = hr.get("total", [0, 0, 0])[2] if len(hr.get("total", [])) > 2 else 0

    def fmt_hr(h):
        if h is None: return "?"
        if h >= 1000: return f"{h/1000:.2f} kH/s"
        return f"{h:.1f} H/s"

    lines.append(f"📊 Hashrate: {fmt_hr(hr_10s)} (10s) | {fmt_hr(hr_60s)} (1m) | {fmt_hr(hr_15m)} (15m)")

    # Results
    results = data.get("results", {})
    accepted = results.get("shares_good", 0)
    rejected = results.get("shares_total", 0) - accepted
    best_diff = results.get("best", [0])[0] if results.get("best") else 0
    lines.append(f"✅ Shares: {accepted} accepted | {rejected} rejected")
    if best_diff:
        lines.append(f"🏆 Best share: {best_diff:,}")

    # Connection
    connection = data.get("connection", {})
    pool = connection.get("pool", "unknown")
    diff = connection.get("diff", 0)
    uptime_s = data.get("uptime", 0)
    h = uptime_s // 3600
    m = (uptime_s % 3600) // 60
    lines.append(f"🌐 Pool: {pool}")
    lines.append(f"🎯 Difficulty: {diff:,}")
    lines.append(f"⏱️  Uptime: {h}h {m}m")

    # CPU info
    cpu = data.get("cpu", {})
    cpu_name = cpu.get("brand", "unknown")
    threads = data.get("threads", 0)
    lines.append(f"🖥️  CPU: {cpu_name} | {threads} threads mining")

    # Version / algo
    algo = data.get("algo", "unknown")
    version = data.get("version", "?")
    lines.append(f"⚙️  XMRig v{version} | algo: {algo}")

else:
    lines.append("⚠️  XMRig API unreachable — checking process...")

    # Check if xmrig is running
    proc = run("pgrep -a xmrig 2>/dev/null | head -3")
    if proc:
        lines.append(f"✅ XMRig process detected:")
        for p in proc.split("\n")[:3]:
            lines.append(f"   {p[:100]}")
    else:
        lines.append("❌ XMRig not running (no process found)")

    # Check recent logs
    log_paths = [
        "/var/log/xmrig.log",
        "/home/switchhacker/xmrig.log",
        "/root/xmrig.log",
    ]
    for lp in log_paths:
        if os.path.exists(lp):
            try:
                tail = run(f"tail -5 {lp}")
                if tail:
                    lines.append(f"📋 Recent log ({lp}):")
                    for l in tail.split("\n"):
                        lines.append(f"   {l[:100]}")
                    break
            except Exception:
                pass

    # Try journalctl
    journal = run("journalctl -u xmrig -n 5 --no-pager 2>/dev/null")
    if journal and len(journal) > 10:
        lines.append("📋 systemd journal (xmrig):")
        for l in journal.split("\n")[-5:]:
            if l.strip():
                lines.append(f"   {l[:100]}")

lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
print("\n".join(lines))
