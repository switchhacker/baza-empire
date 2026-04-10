#!/usr/bin/env python3
"""Skill: contract_clause — Generate common contract clauses.
Usage: ##SKILL:contract_clause{"type":"change_order"}##"""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
ctype = args.get("type","general")
clauses = {
    "change_order": "Any changes to the scope of work described herein must be documented in writing as a Change Order, signed by both parties, before work begins. Additional costs or schedule adjustments will be detailed in the Change Order.",
    "payment": "Payment schedule: 50% deposit due upon contract signing, 25% upon substantial completion of rough work, 25% upon final completion and client walkthrough approval.",
    "warranty": "Contractor warrants all workmanship for a period of one (1) year from the date of substantial completion. This warranty does not cover normal wear and tear, acts of God, or damage caused by the Owner.",
    "termination": "Either party may terminate this agreement with 14 days written notice. In the event of termination, the Owner shall pay for all work completed and materials ordered to date.",
    "permits": "Contractor shall obtain all necessary permits and inspections required by local authorities. Permit fees are included in the contract price unless otherwise noted.",
    "insurance": "Contractor maintains general liability insurance ($1M/$2M) and workers compensation coverage. Certificates available upon request.",
    "dispute": "Any disputes arising from this agreement shall first be addressed through mediation. If mediation fails, disputes shall be resolved through binding arbitration in Bucks County, PA.",
    "force_majeure": "Neither party shall be liable for delays or failure to perform due to circumstances beyond reasonable control, including but not limited to: acts of God, pandemic, government orders, supply chain disruptions, or labor strikes.",
}
clause = clauses.get(ctype, clauses["general"] if "general" in clauses else "Clause type not found. Available: " + ", ".join(clauses.keys()))
print(f"CONTRACT CLAUSE — {ctype.replace('_',' ').title()}")
print(f"{'='*50}")
print(f"\n{clause}")
