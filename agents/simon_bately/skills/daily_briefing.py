#!/usr/bin/env python3
"""
Simon Bately Skill: daily_briefing
Runs all briefing data sources and prints a combined morning briefing.
"""
import os
import sys
import json
import subprocess
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SHARED_DIR = os.path.join(BASE_DIR, "skills", "shared")
PYTHON = sys.executable

def run_skill(script_name, args={}):
    path = os.path.join(SHARED_DIR, script_name)
    if not os.path.exists(path):
        return f"[{script_name}] not found"
    env = os.environ.copy()
    env["SKILL_ARGS"] = json.dumps(args)
    # Pass EMPIRE_LOCATION and XMR_WALLET_ADDRESS explicitly
    env["EMPIRE_LOCATION"] = os.environ.get("EMPIRE_LOCATION", "Philadelphia, PA")
    env["XMR_WALLET_ADDRESS"] = os.environ.get("XMR_WALLET_ADDRESS", "")
    result = subprocess.run(
        [PYTHON, path],
        capture_output=True, text=True, timeout=20, env=env
    )
    output = result.stdout.strip() or result.stderr.strip() or "(no output)"
    # Strip the === header lines from sub-skills for cleaner output
    lines = [l for l in output.splitlines() if not l.startswith("===")]
    return "\n".join(lines).strip()

location = os.environ.get("EMPIRE_LOCATION", "Philadelphia, PA")
now = datetime.now().strftime("%A, %B %d %Y — %I:%M %p")

print(f"🌅 Good morning, Serge. Here's your {now} briefing.\n")

# Crypto prices
print("💰 CRYPTO PRICES")
print(run_skill("crypto_prices.py", {
    "coins": ["bitcoin", "ethereum", "monero", "ravencoin", "litecoin"]
}))

# Mining earnings
print("\n⛏️  MINING EARNINGS")
wallet = os.environ.get("XMR_WALLET_ADDRESS", "")
if wallet:
    print(run_skill("mining_earnings.py"))
else:
    print("  Wallet not configured — set XMR_WALLET_ADDRESS in /etc/baza-agents.env")

# Weather
print(f"\n🌤️  WEATHER — {location}")
print(run_skill("weather.py", {"location": location}))

# News
print("\n📰 NEWS")
print(run_skill("news.py", {"category": "crypto"}))

print("\n✅ Briefing complete.")
