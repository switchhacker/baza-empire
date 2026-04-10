#!/usr/bin/env python3
"""Skill: code_line_count — Count lines of code by language.
Usage: ##SKILL:code_line_count{"path":"."}##"""
import os, json, subprocess
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
path = args.get("path",".")
exts = {".py":"Python",".js":"JavaScript",".ts":"TypeScript",".html":"HTML",".css":"CSS",".sh":"Shell",".sql":"SQL",".yaml":"YAML",".json":"JSON",".md":"Markdown"}
counts = {}
for root, dirs, files in os.walk(path):
    dirs[:] = [d for d in dirs if d not in (".git","__pycache__","node_modules","venv",".venv")]
    for f in files:
        ext = os.path.splitext(f)[1].lower()
        if ext in exts:
            try:
                with open(os.path.join(root,f), errors="replace") as fh:
                    lines = sum(1 for _ in fh)
                lang = exts[ext]
                counts[lang] = counts.get(lang,0) + lines
            except: pass
total = sum(counts.values())
print(f"Lines of Code: {total:,}")
for lang in sorted(counts, key=counts.get, reverse=True):
    pct = counts[lang]*100/total if total else 0
    bar = "█" * int(pct/3)
    print(f"  {lang:<15} {counts[lang]:>7,} ({pct:.0f}%) {bar}")
