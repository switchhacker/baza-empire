#!/usr/bin/env python3
"""Skill: normalize_phone — Format phone numbers to E.164.
Usage: ##SKILL:normalize_phone{"phone":"(215) 555-1234"}##"""
import os, json, re
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
phone = args.get("phone","")
digits = re.sub(r"\D","",phone)
if len(digits) == 10: formatted = f"+1{digits}"
elif len(digits) == 11 and digits[0] == "1": formatted = f"+{digits}"
else: formatted = f"+{digits}"
display = f"({digits[-10:-7]}) {digits[-7:-4]}-{digits[-4:]}" if len(digits) >= 10 else digits
print(f"Input: {phone}")
print(f"E.164: {formatted}")
print(f"Display: {display}")
