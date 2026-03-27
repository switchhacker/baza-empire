#!/usr/bin/env python3
"""
Baza Empire Skill — mining_earnings
Calculates mining earnings from XMRig stats + current XMR price.
Usage: ##SKILL:mining_earnings{}##
"""
import os, json, urllib.request

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
XMRIG_API = args.get("api_url", "http://localhost:4067/2/summary")
POWER_WATTS = args.get("power_watts", 350)      # estimated rig draw in watts
ELEC_RATE   = args.get("elec_rate", 0.16)       # $/kWh (PECO Philadelphia rate)

lines = []
lines.append("💰 MINING EARNINGS ESTIMATE")
lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")

def fetch(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None

# 1 — Get current hashrate from XMRig API
hashrate_hs = 0
algo = "rx/0"
try:
    xmrig = fetch(XMRIG_API)
    if xmrig:
        hr = xmrig.get("hashrate", {}).get("total", [0, 0, 0])
        # prefer 15-min average for stability
        hashrate_hs = hr[2] if len(hr) > 2 and hr[2] else (hr[1] if len(hr) > 1 and hr[1] else hr[0])
        algo = xmrig.get("algo", "rx/0")
        lines.append(f"📊 Current hashrate: {hashrate_hs:.1f} H/s ({algo})")
    else:
        # Fallback: use known baza rig hashrate for RandomX (XMR)
        hashrate_hs = 7500  # ~7.5 kH/s typical for RX 6700 XT + RTX 3070 combined
        lines.append(f"📊 Hashrate (estimated — XMRig offline): {hashrate_hs:.0f} H/s")
except Exception as e:
    hashrate_hs = 7500
    lines.append(f"📊 Hashrate (fallback): {hashrate_hs:.0f} H/s")

# 2 — Get XMR network stats from minexmr/supportxmr-style API
# Use monero.com public API for network difficulty
network_difficulty = 0
network_hashrate = 0
block_reward_xmr = 0.6  # approximate current tail emission

try:
    # Try monero public API
    net = fetch("https://localmonero.co/blocks/api/get_stats", timeout=6)
    if net:
        network_hashrate = float(net.get("hash_rate", 0))
        network_difficulty = float(net.get("difficulty", 0))
        block_reward_xmr = float(net.get("last_reward", 0)) / 1e12  # piconero to XMR
        lines.append(f"🌐 Network hashrate: {network_hashrate/1e9:.2f} GH/s | difficulty: {network_difficulty:.2e}")
    else:
        raise Exception("API unavailable")
except Exception:
    # Use known approximate values
    network_hashrate = 3.2e9   # ~3.2 GH/s (approximate mid-2025 XMR network)
    block_reward_xmr = 0.6
    lines.append(f"🌐 Network hashrate (estimated): {network_hashrate/1e9:.1f} GH/s")

# 3 — Get XMR price in USD
xmr_price_usd = 0
try:
    cg = fetch("https://api.coingecko.com/api/v3/simple/price?ids=monero&vs_currencies=usd", timeout=6)
    if cg and "monero" in cg:
        xmr_price_usd = float(cg["monero"]["usd"])
        lines.append(f"💵 XMR price: ${xmr_price_usd:,.2f}")
    else:
        raise Exception("CoinGecko unavailable")
except Exception:
    xmr_price_usd = 280.0  # fallback approximate
    lines.append(f"💵 XMR price: ~${xmr_price_usd:.0f} (estimated — live API unavailable)")

# 4 — Calculate earnings
# Blocks per day = 86400 / 120 = 720 (XMR ~2min block time)
BLOCKS_PER_DAY = 720
POOL_FEE = 0.01  # 1% typical pool fee

if network_hashrate > 0 and hashrate_hs > 0:
    # My share of network
    my_share = hashrate_hs / network_hashrate
    xmr_per_day = my_share * BLOCKS_PER_DAY * block_reward_xmr * (1 - POOL_FEE)
    xmr_per_month = xmr_per_day * 30.44

    usd_per_day = xmr_per_day * xmr_price_usd
    usd_per_month = xmr_per_month * xmr_price_usd

    # Electricity cost
    kwh_per_day = (POWER_WATTS / 1000) * 24
    elec_cost_day = kwh_per_day * ELEC_RATE
    elec_cost_month = elec_cost_day * 30.44

    net_usd_day = usd_per_day - elec_cost_day
    net_usd_month = usd_per_month - elec_cost_month

    lines.append("")
    lines.append("📈 DAILY EARNINGS")
    lines.append(f"   Gross:       {xmr_per_day:.6f} XMR = ${usd_per_day:.2f}")
    lines.append(f"   Electric:   -${elec_cost_day:.2f} ({kwh_per_day:.1f} kWh @ ${ELEC_RATE}/kWh)")
    lines.append(f"   Net:         ${net_usd_day:.2f}/day")
    lines.append("")
    lines.append("📅 MONTHLY PROJECTION")
    lines.append(f"   Gross:       {xmr_per_month:.4f} XMR = ${usd_per_month:.2f}")
    lines.append(f"   Electric:   -${elec_cost_month:.2f}")
    lines.append(f"   Net:         ${net_usd_month:.2f}/month")
    lines.append("")
    lines.append(f"⚙️  Based on: {hashrate_hs:.0f} H/s | {POWER_WATTS}W | ${ELEC_RATE}/kWh | 1% pool fee")
else:
    lines.append("⚠️  Cannot calculate — network hashrate unavailable")

lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
print("\n".join(lines))
