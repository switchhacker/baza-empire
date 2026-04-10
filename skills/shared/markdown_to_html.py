#!/usr/bin/env python3
"""Convert markdown to HTML."""
import os, json, re
args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
md = args.get("text", args.get("markdown", ""))
html = md
html = re.sub(r'^### (.+)$', r'<h3>\1</h3>', html, flags=re.MULTILINE)
html = re.sub(r'^## (.+)$', r'<h2>\1</h2>', html, flags=re.MULTILINE)
html = re.sub(r'^# (.+)$', r'<h1>\1</h1>', html, flags=re.MULTILINE)
html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html)
html = re.sub(r'\*(.+?)\*', r'<em>\1</em>', html)
html = re.sub(r'^- (.+)$', r'<li>\1</li>', html, flags=re.MULTILINE)
html = re.sub(r'\n', r'<br>', html)
print(json.dumps({"html": html}))
