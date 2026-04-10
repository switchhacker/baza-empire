#!/usr/bin/env python3
"""Skill: cabinet_estimator — Kitchen cabinet rough estimate.
Usage: ##SKILL:cabinet_estimator{"linear_ft":25,"grade":"mid"}##"""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
linear = float(args.get("linear_ft",0))
grade = args.get("grade","mid").lower()
rates = {"economy":{"low":100,"high":200},"mid":{"low":200,"high":500},"premium":{"low":500,"high":1200},"custom":{"low":1000,"high":2500}}
r = rates.get(grade,rates["mid"])
uppers = int(linear * 0.6)
lowers = int(linear * 0.7)
print(f"Kitchen Cabinet Estimate")
print(f"  Linear feet: {linear:.0f}ft | Grade: {grade}")
print(f"  Upper cabinets: ~{uppers} units")
print(f"  Lower/base: ~{lowers} units")
print(f"  Material range: ${linear*r['low']:,.0f} - ${linear*r['high']:,.0f}")
print(f"  Installation: ${linear*75:,.0f} - ${linear*150:,.0f}")
print(f"  Total estimate: ${linear*(r['low']+75):,.0f} - ${linear*(r['high']+150):,.0f}")
