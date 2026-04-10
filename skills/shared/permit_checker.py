#!/usr/bin/env python3
"""Check PA permit requirements for a project type."""
import os, json

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
project_type = args.get("type", "").lower()
value = float(args.get("value", 0))

PA_PERMITS = {
    "new_construction": {"permit": True, "type": "Building Permit", "inspections": ["foundation", "framing", "electrical", "plumbing", "final"], "notes": "UCC compliance required in PA"},
    "renovation": {"permit": True, "type": "Building Permit", "inspections": ["framing", "electrical", "plumbing", "final"], "notes": "Required if structural changes"},
    "electrical": {"permit": True, "type": "Electrical Permit", "inspections": ["rough-in", "final"], "notes": "Must be done by licensed electrician"},
    "plumbing": {"permit": True, "type": "Plumbing Permit", "inspections": ["rough-in", "final"], "notes": "Must be done by licensed plumber"},
    "roofing": {"permit": True, "type": "Building Permit", "inspections": ["final"], "notes": "Required for structural changes"},
    "deck": {"permit": True, "type": "Building Permit", "inspections": ["footing", "framing", "final"], "notes": "Footings below frost line (36in PA)"},
    "fence": {"permit": False, "type": "None", "inspections": [], "notes": "Check local zoning for height restrictions"},
    "painting": {"permit": False, "type": "None", "inspections": [], "notes": "Lead paint rules apply for pre-1978 homes"},
    "hvac": {"permit": True, "type": "Mechanical Permit", "inspections": ["rough-in", "final"], "notes": "EPA 608 certification required"},
    "demolition": {"permit": True, "type": "Demolition Permit", "inspections": ["pre-demo", "final"], "notes": "Asbestos survey may be required"},
}

key = project_type.replace(" ", "_")
info = PA_PERMITS.get(key, {"permit": True, "type": "Check with local municipality", "inspections": ["final"], "notes": f"Unknown type: {project_type}"})
info["project_type"] = project_type
info["estimated_value"] = value
if value > 0:
    info["permit_fee_estimate"] = round(max(50, value * 0.01), 2)

print(json.dumps(info))
