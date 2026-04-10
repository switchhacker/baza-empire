#!/usr/bin/env python3
"""Skill: ssl_expiry — Check SSL certificate expiration.
Usage: ##SKILL:ssl_expiry{"domain":"example.com"}##"""
import os, json, ssl, socket
from datetime import datetime
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
domain = args.get("domain","")
if not domain: print("Error: domain required"); exit(1)
try:
    ctx = ssl.create_default_context()
    with ctx.wrap_socket(socket.socket(), server_hostname=domain) as s:
        s.settimeout(5); s.connect((domain, 443))
        cert = s.getpeercert()
    exp = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
    days = (exp - datetime.now()).days
    icon = "🟢" if days > 30 else "🟡" if days > 7 else "🔴"
    print(f"{icon} {domain}")
    print(f"  Expires: {exp.strftime('%Y-%m-%d')} ({days} days)")
    print(f"  Issuer: {dict(cert['issuer'][0]).get('commonName','?') if cert.get('issuer') else '?'}")
except Exception as e: print(f"Error: {e}")
