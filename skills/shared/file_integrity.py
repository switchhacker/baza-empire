#!/usr/bin/env python3
"""Skill: file_integrity — SHA256 hashes for key files.
Usage: ##SKILL:file_integrity{"files":["/etc/passwd","/etc/ssh/sshd_config"]}##"""
import os, json, hashlib
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
files = args.get("files",["/etc/passwd","/etc/ssh/sshd_config","/etc/hosts"])
print("File Integrity Check")
for f in files:
    try:
        h = hashlib.sha256(open(f,"rb").read()).hexdigest()
        size = os.path.getsize(f)
        print(f"  {f}")
        print(f"    SHA256: {h[:32]}...")
        print(f"    Size: {size} bytes")
    except Exception as e:
        print(f"  {f}: {e}")
