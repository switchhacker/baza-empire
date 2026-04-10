#!/usr/bin/env python3
"""Convert between measurement units (sq ft, linear ft, yards, meters)."""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
value = float(args.get("value", 0)); from_u = args.get("from", "sqft"); to_u = args.get("to", "sqm")
conversions = {"sqft_sqm": 0.0929, "sqm_sqft": 10.764, "ft_m": 0.3048, "m_ft": 3.2808, "ft_in": 12, "in_ft": 1/12, "yd_ft": 3, "ft_yd": 1/3, "sqft_sqyd": 1/9, "sqyd_sqft": 9, "mi_km": 1.609, "km_mi": 0.6214, "gal_l": 3.785, "l_gal": 0.2642, "lb_kg": 0.4536, "kg_lb": 2.205}
key = f"{from_u}_{to_u}"
if key in conversions: print(json.dumps({"result": round(value * conversions[key], 4), "from": f"{value} {from_u}", "to": f"{round(value * conversions[key], 4)} {to_u}"}))
else: print(json.dumps({"error": f"Unknown conversion: {from_u} to {to_u}", "available": list(conversions.keys())}))
