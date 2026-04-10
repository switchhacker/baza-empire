#!/usr/bin/env python3
"""Skill: prompt_template — Fill a prompt template with variables.
Usage: ##SKILL:prompt_template{"template":"Hello {name}, your project {project} is {status}","vars":{"name":"John","project":"kitchen","status":"on track"}}##"""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
template = args.get("template","")
variables = args.get("vars",{})
if not template: print("Error: template required"); exit(1)
try:
    result = template.format(**variables)
    print(result)
except KeyError as e:
    print(f"Error: missing variable {e}")
    print(f"Template needs: {[k for k in template.split('{') if '}' in k]}")
