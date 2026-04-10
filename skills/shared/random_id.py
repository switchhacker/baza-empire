#!/usr/bin/env python3
"""Generate various ID formats (UUID, short ID, invoice number)."""
import os, json, uuid, secrets, string
from datetime import datetime
args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
fmt = args.get("format", "uuid")
if fmt == "uuid": result = str(uuid.uuid4())
elif fmt == "short": result = secrets.token_hex(4)
elif fmt == "invoice": result = f"INV-{datetime.now().strftime('%Y%m%d')}-{secrets.token_hex(2).upper()}"
elif fmt == "project": result = f"PRJ-{secrets.token_hex(3).upper()}"
else: result = str(uuid.uuid4())
print(json.dumps({"id": result, "format": fmt}))
