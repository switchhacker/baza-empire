#!/usr/bin/env python3
"""Strip HTML tags, return plain text."""
import os, json, re
args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
html = args.get("html", args.get("text", ""))
text = re.sub(r'<br\s*/?>', '\n', html)
text = re.sub(r'<[^>]+>', '', text)
text = re.sub(r'&amp;', '&', text); text = re.sub(r'&lt;', '<', text); text = re.sub(r'&gt;', '>', text)
text = re.sub(r'&nbsp;', ' ', text); text = re.sub(r'&#\d+;', '', text)
print(json.dumps({"text": text.strip()}))
