#!/usr/bin/env python3
"""Resize an image to specified dimensions."""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
path = args.get("path", ""); width = int(args.get("width", 800)); height = int(args.get("height", 0))
output = args.get("output", path.replace(".", "_resized.") if path else "")
if not path: print(json.dumps({"error": "path required"}))
else:
    from PIL import Image
    img = Image.open(path)
    if height == 0: ratio = width / img.width; height = int(img.height * ratio)
    img = img.resize((width, height), Image.LANCZOS); img.save(output)
    print(json.dumps({"file": output, "width": width, "height": height}))
