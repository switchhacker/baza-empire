#!/usr/bin/env python3
"""Generate meeting notes from template and args."""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
template = args.get("template", "")
data = args.get("data", {})
subject = args.get("subject", "")
to = args.get("to", "")
body = template
for k, v in data.items(): body = body.replace("{{" + k + "}}", str(v))
if not body: body = f"Subject: {subject}\nTo: {to}\n\n[Generated meeting notes — fill in details]"
print(json.dumps({"subject": subject, "to": to, "body": body, "type": "meeting_notes"}))
