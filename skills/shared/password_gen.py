#!/usr/bin/env python3
"""Generate a secure random password."""
import os, json, secrets, string
args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
length = int(args.get("length", 16))
chars = string.ascii_letters + string.digits + string.punctuation
password = ''.join(secrets.choice(chars) for _ in range(length))
print(json.dumps({"password": password, "length": length}))
