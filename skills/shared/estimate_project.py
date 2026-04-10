#!/usr/bin/env python3
"""
Baza Empire — EstimatOR Skill
Generates structured construction cost estimates for AHBCO LLC projects.
Uses Philadelphia-area cost tables for residential GC work.

SKILL_ARGS:
  description: "Kitchen remodel, 12x15, gut to studs, new cabinets, quartz counters..."
  scope: "kitchen" | "bathroom" | "addition" | "basement" | "deck" | "full-reno" | "other"
  sqft: 180  (optional, auto-infer from description if missing)
"""
import os
import sys
import json
import re

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
description = args.get("description", "")
scope = args.get("scope", "other").lower()
sqft = args.get("sqft", 0)

# ── Philadelphia-area cost tables (2026 residential) ──────────────────────────

COST_TABLES = {
    "kitchen": {
        "Demo & Haul": {"rate_low": 15, "rate_high": 25, "unit": "sqft", "applies": True},
        "Framing / Structural": {"rate_low": 8, "rate_high": 15, "unit": "sqft", "applies": True},
        "Electrical Rough-In": {"rate_low": 3000, "rate_high": 8000, "unit": "job", "qty": 1},
        "Plumbing Rough-In": {"rate_low": 2000, "rate_high": 5000, "unit": "job", "qty": 1},
        "HVAC Adjustments": {"rate_low": 1500, "rate_high": 4000, "unit": "job", "qty": 1},
        "Drywall & Finish": {"rate_low": 3, "rate_high": 5, "unit": "sqft", "applies": True},
        "Flooring": {"rate_low": 8, "rate_high": 15, "unit": "sqft", "applies": True},
        "Paint": {"rate_low": 2, "rate_high": 4, "unit": "sqft", "applies": True},
        "Cabinets": {"rate_low": 150, "rate_high": 500, "unit": "linear ft", "qty_factor": 0.8},
        "Countertops (Quartz)": {"rate_low": 50, "rate_high": 150, "unit": "sqft", "qty_factor": 0.3},
        "Fixtures & Hardware": {"rate_low": 2000, "rate_high": 6000, "unit": "job", "qty": 1},
        "Appliances (Allowance)": {"rate_low": 3000, "rate_high": 10000, "unit": "job", "qty": 1},
        "Permits": {"rate_low": 500, "rate_high": 1500, "unit": "job", "qty": 1},
    },
    "bathroom": {
        "Demo & Haul": {"rate_low": 15, "rate_high": 25, "unit": "sqft", "applies": True},
        "Framing / Structural": {"rate_low": 8, "rate_high": 12, "unit": "sqft", "applies": True},
        "Electrical Rough-In": {"rate_low": 1500, "rate_high": 4000, "unit": "job", "qty": 1},
        "Plumbing Rough-In": {"rate_low": 3000, "rate_high": 7000, "unit": "job", "qty": 1},
        "Waterproofing": {"rate_low": 1000, "rate_high": 3000, "unit": "job", "qty": 1},
        "Tile Work": {"rate_low": 12, "rate_high": 30, "unit": "sqft", "applies": True},
        "Drywall & Finish": {"rate_low": 3, "rate_high": 5, "unit": "sqft", "applies": True},
        "Paint": {"rate_low": 2, "rate_high": 4, "unit": "sqft", "applies": True},
        "Vanity & Countertop": {"rate_low": 800, "rate_high": 4000, "unit": "job", "qty": 1},
        "Fixtures (Toilet/Faucet/Shower)": {"rate_low": 1500, "rate_high": 5000, "unit": "job", "qty": 1},
        "Ventilation Fan": {"rate_low": 200, "rate_high": 600, "unit": "job", "qty": 1},
        "Permits": {"rate_low": 400, "rate_high": 1000, "unit": "job", "qty": 1},
    },
    "addition": {
        "Foundation": {"rate_low": 20, "rate_high": 40, "unit": "sqft", "applies": True},
        "Framing": {"rate_low": 15, "rate_high": 30, "unit": "sqft", "applies": True},
        "Roofing": {"rate_low": 8, "rate_high": 15, "unit": "sqft", "applies": True},
        "Siding / Exterior": {"rate_low": 10, "rate_high": 20, "unit": "sqft", "applies": True},
        "Windows & Doors": {"rate_low": 3000, "rate_high": 10000, "unit": "job", "qty": 1},
        "Electrical": {"rate_low": 5000, "rate_high": 12000, "unit": "job", "qty": 1},
        "Plumbing": {"rate_low": 3000, "rate_high": 8000, "unit": "job", "qty": 1},
        "HVAC Extension": {"rate_low": 3000, "rate_high": 10000, "unit": "job", "qty": 1},
        "Insulation": {"rate_low": 2, "rate_high": 5, "unit": "sqft", "applies": True},
        "Drywall & Finish": {"rate_low": 3, "rate_high": 5, "unit": "sqft", "applies": True},
        "Flooring": {"rate_low": 6, "rate_high": 15, "unit": "sqft", "applies": True},
        "Paint": {"rate_low": 2, "rate_high": 4, "unit": "sqft", "applies": True},
        "Permits & Engineering": {"rate_low": 2000, "rate_high": 5000, "unit": "job", "qty": 1},
    },
    "basement": {
        "Waterproofing": {"rate_low": 3, "rate_high": 8, "unit": "sqft", "applies": True},
        "Framing": {"rate_low": 5, "rate_high": 12, "unit": "sqft", "applies": True},
        "Electrical": {"rate_low": 3000, "rate_high": 8000, "unit": "job", "qty": 1},
        "Plumbing (if bathroom)": {"rate_low": 2000, "rate_high": 6000, "unit": "job", "qty": 1},
        "HVAC": {"rate_low": 2000, "rate_high": 6000, "unit": "job", "qty": 1},
        "Insulation": {"rate_low": 2, "rate_high": 4, "unit": "sqft", "applies": True},
        "Drywall": {"rate_low": 3, "rate_high": 5, "unit": "sqft", "applies": True},
        "Flooring": {"rate_low": 4, "rate_high": 12, "unit": "sqft", "applies": True},
        "Paint": {"rate_low": 2, "rate_high": 3, "unit": "sqft", "applies": True},
        "Egress Window": {"rate_low": 2000, "rate_high": 5000, "unit": "job", "qty": 1},
        "Permits": {"rate_low": 500, "rate_high": 1500, "unit": "job", "qty": 1},
    },
    "deck": {
        "Demo (if replacing)": {"rate_low": 5, "rate_high": 10, "unit": "sqft", "applies": True},
        "Footings & Foundation": {"rate_low": 8, "rate_high": 15, "unit": "sqft", "applies": True},
        "Framing & Structure": {"rate_low": 10, "rate_high": 20, "unit": "sqft", "applies": True},
        "Decking Material": {"rate_low": 8, "rate_high": 25, "unit": "sqft", "applies": True},
        "Railing": {"rate_low": 30, "rate_high": 80, "unit": "linear ft", "qty_factor": 0.5},
        "Stairs": {"rate_low": 500, "rate_high": 2000, "unit": "job", "qty": 1},
        "Electrical (Outlets/Lighting)": {"rate_low": 500, "rate_high": 2000, "unit": "job", "qty": 1},
        "Stain/Seal": {"rate_low": 2, "rate_high": 5, "unit": "sqft", "applies": True},
        "Permits": {"rate_low": 300, "rate_high": 1000, "unit": "job", "qty": 1},
    },
}

# Default for full-reno and other
COST_TABLES["full-reno"] = {
    "Demo & Haul (Full)": {"rate_low": 10, "rate_high": 20, "unit": "sqft", "applies": True},
    "Structural / Framing": {"rate_low": 12, "rate_high": 25, "unit": "sqft", "applies": True},
    "Electrical (Full Rewire)": {"rate_low": 8000, "rate_high": 20000, "unit": "job", "qty": 1},
    "Plumbing (Full)": {"rate_low": 6000, "rate_high": 15000, "unit": "job", "qty": 1},
    "HVAC (Full System)": {"rate_low": 8000, "rate_high": 20000, "unit": "job", "qty": 1},
    "Insulation": {"rate_low": 2, "rate_high": 5, "unit": "sqft", "applies": True},
    "Drywall": {"rate_low": 3, "rate_high": 5, "unit": "sqft", "applies": True},
    "Flooring (All Rooms)": {"rate_low": 6, "rate_high": 15, "unit": "sqft", "applies": True},
    "Paint (Full Interior)": {"rate_low": 2, "rate_high": 4, "unit": "sqft", "applies": True},
    "Kitchen (Allowance)": {"rate_low": 15000, "rate_high": 50000, "unit": "job", "qty": 1},
    "Bathroom x2 (Allowance)": {"rate_low": 10000, "rate_high": 30000, "unit": "job", "qty": 1},
    "Windows & Doors": {"rate_low": 5000, "rate_high": 15000, "unit": "job", "qty": 1},
    "Exterior (Siding/Roof)": {"rate_low": 10000, "rate_high": 30000, "unit": "job", "qty": 1},
    "Permits & Engineering": {"rate_low": 3000, "rate_high": 8000, "unit": "job", "qty": 1},
}
COST_TABLES["other"] = COST_TABLES["full-reno"]


# ── Extract square footage from description ──────────────────────────────────

def extract_sqft(desc):
    """Try to extract square footage from description like '12x15' or '180 sqft'."""
    # Match "12x15" or "12 x 15"
    m = re.search(r'(\d+)\s*[xX]\s*(\d+)', desc)
    if m:
        return int(m.group(1)) * int(m.group(2))
    # Match "180 sqft" or "180 sq ft" or "180 square feet"
    m = re.search(r'(\d+)\s*(?:sqft|sq\.?\s*ft|square\s*feet)', desc, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return 0


# ── Generate estimate ────────────────────────────────────────────────────────

if not description:
    print(json.dumps({"error": "description is required"}))
    sys.exit(1)

if not sqft:
    sqft = extract_sqft(description)
if not sqft:
    # Default sqft by scope
    defaults = {"kitchen": 150, "bathroom": 60, "addition": 300, "basement": 500, "deck": 200, "full-reno": 1500}
    sqft = defaults.get(scope, 200)

table = COST_TABLES.get(scope, COST_TABLES["other"])

line_items = []
subtotal_low = 0
subtotal_high = 0

for item_name, costs in table.items():
    if costs.get("applies"):
        qty = sqft
    elif "qty_factor" in costs:
        qty = round(sqft * costs["qty_factor"])
    else:
        qty = costs.get("qty", 1)

    low = round(qty * costs["rate_low"])
    high = round(qty * costs["rate_high"])
    mid = round((low + high) / 2)

    line_items.append({
        "category": item_name,
        "qty": qty,
        "unit": costs["unit"],
        "rate_low": costs["rate_low"],
        "rate_high": costs["rate_high"],
        "total_low": low,
        "total_high": high,
        "total_mid": mid,
    })
    subtotal_low += low
    subtotal_high += high

# Add contingency (10%)
contingency_low = round(subtotal_low * 0.10)
contingency_high = round(subtotal_high * 0.10)
line_items.append({
    "category": "Contingency (10%)",
    "qty": 1, "unit": "job",
    "rate_low": contingency_low, "rate_high": contingency_high,
    "total_low": contingency_low, "total_high": contingency_high,
    "total_mid": round((contingency_low + contingency_high) / 2),
})

subtotal_low += contingency_low
subtotal_high += contingency_high

# GC markup (15%)
markup_low = round(subtotal_low * 0.15)
markup_high = round(subtotal_high * 0.15)

grand_low = subtotal_low + markup_low
grand_high = subtotal_high + markup_high
grand_mid = round((grand_low + grand_high) / 2)

result = {
    "scope": scope,
    "sqft": sqft,
    "description": description,
    "line_items": line_items,
    "subtotal_low": subtotal_low,
    "subtotal_high": subtotal_high,
    "markup_pct": 15,
    "markup_low": markup_low,
    "markup_high": markup_high,
    "grand_total_low": grand_low,
    "grand_total_high": grand_high,
    "grand_total_mid": grand_mid,
    "location": "Philadelphia PA",
    "note": "Estimates based on 2026 Philadelphia-area residential GC rates. Actual costs may vary based on material selection, site conditions, and labor availability.",
}

# Print summary for LLM context
print(f"ESTIMATE: {scope.upper()} — {sqft} sqft")
print(f"Range: ${grand_low:,.0f} - ${grand_high:,.0f} (mid: ${grand_mid:,.0f})")
print(f"Breakdown ({len(line_items)} line items):")
for item in line_items:
    print(f"  {item['category']}: ${item['total_low']:,.0f} - ${item['total_high']:,.0f}")
print(f"Subtotal: ${subtotal_low:,.0f} - ${subtotal_high:,.0f}")
print(f"GC Markup (15%): ${markup_low:,.0f} - ${markup_high:,.0f}")
print(f"GRAND TOTAL: ${grand_low:,.0f} - ${grand_high:,.0f}")
print()
print(json.dumps(result, indent=2))
