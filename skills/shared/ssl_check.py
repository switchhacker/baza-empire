#!/usr/bin/env python3
"""Check SSL certificate expiry for a domain."""
import os, json, ssl, socket
from datetime import datetime

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
domain = args.get("domain", "")
port = int(args.get("port", 443))

if not domain:
    print(json.dumps({"error": "No domain provided"}))
else:
    try:
        ctx = ssl.create_default_context()
        with ctx.wrap_socket(socket.socket(), server_hostname=domain) as s:
            s.settimeout(10)
            s.connect((domain, port))
            cert = s.getpeercert()
        expiry = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
        issued = datetime.strptime(cert["notBefore"], "%b %d %H:%M:%S %Y %Z")
        days_left = (expiry - datetime.now()).days
        print(json.dumps({
            "domain": domain, "issuer": dict(x[0] for x in cert.get("issuer", [])),
            "subject": dict(x[0] for x in cert.get("subject", [])),
            "issued": issued.isoformat(), "expires": expiry.isoformat(),
            "days_remaining": days_left,
            "status": "valid" if days_left > 0 else "expired",
            "warning": days_left < 30
        }))
    except Exception as e:
        print(json.dumps({"domain": domain, "error": str(e)}))
