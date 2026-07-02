#!/usr/bin/env python3
"""Send a message to a Telegram chat via bot API (markdown → rich HTML)."""
import os, sys, json
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
token = args.get("token", os.environ.get("TELEGRAM_SIMON_BATELY", ""))
chat_id = args.get("chat_id", "")
text = args.get("text", "")
if not text:
    print(json.dumps({"error": "text required"}))
else:
    try:
        from core.telegram_fmt import post_html
        ok = post_html(token, chat_id, text)
        print(json.dumps({"success": bool(ok)}))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
