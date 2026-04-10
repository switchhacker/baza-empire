#!/usr/bin/env python3
"""Skill: zip_code_lookup — City/state from US zip code.
Usage: ##SKILL:zip_code_lookup{"zip":"19020"}##"""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
z = args.get("zip","")
# Common PA zips + major cities
zips = {"19020":"Bensalem, PA","19116":"Philadelphia, PA","19154":"Philadelphia, PA","19053":"Feasterville, PA","19047":"Langhorne, PA","19057":"Levittown, PA","10001":"New York, NY","90210":"Beverly Hills, CA","60601":"Chicago, IL","33101":"Miami, FL","77001":"Houston, TX","85001":"Phoenix, AZ","15201":"Pittsburgh, PA","17101":"Harrisburg, PA","18501":"Scranton, PA"}
if z in zips:
    print(f"ZIP: {z} → {zips[z]}")
else:
    print(f"ZIP: {z} → (not in local database)")
    print(f"Try: web_search with 'US zip code {z}'")
