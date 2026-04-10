#!/usr/bin/env python3
"""Generate QR code for a URL or text (saves as PNG)."""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
data = args.get("data", args.get("url", args.get("text", "")))
output = args.get("output", "/tmp/qr_code.png")
if not data: print(json.dumps({"error": "data/url/text required"}))
else:
    try:
        import qrcode; img = qrcode.make(data); img.save(output)
        print(json.dumps({"file": output, "data": data}))
    except ImportError: print(json.dumps({"error": "qrcode package not installed. Run: pip install qrcode[pil]"}))
