#!/usr/bin/env python3
"""Skill: lead_score — Score a lead 1-100 based on criteria.
Usage: ##SKILL:lead_score{"budget":"50000","timeline":"1month","source":"referral","project":"kitchen"}##"""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
score = 50
factors = []
budget = float(args.get("budget",0))
if budget >= 50000: score += 20; factors.append(f"+20 high budget (${budget:,.0f})")
elif budget >= 20000: score += 10; factors.append(f"+10 medium budget (${budget:,.0f})")
elif budget > 0: score += 5; factors.append(f"+5 has budget (${budget:,.0f})")
tl = args.get("timeline","").lower()
if "asap" in tl or "week" in tl: score += 15; factors.append("+15 urgent timeline")
elif "month" in tl: score += 10; factors.append("+10 near-term")
src = args.get("source","").lower()
if "referral" in src: score += 15; factors.append("+15 referral source")
elif "google" in src: score += 10; factors.append("+10 organic search")
proj = args.get("project","").lower()
if any(k in proj for k in ["kitchen","bathroom","basement"]): score += 10; factors.append("+10 high-value project type")
score = min(100, max(1, score))
tier = "HOT" if score >= 75 else "WARM" if score >= 50 else "COLD"
print(f"Lead Score: {score}/100 ({tier})")
for f in factors: print(f"  {f}")
print(f"\nRecommendation: {'Call within 1 hour' if tier=='HOT' else 'Follow up within 24h' if tier=='WARM' else 'Add to nurture sequence'}")
