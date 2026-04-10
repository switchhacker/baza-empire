#!/usr/bin/env python3
"""Send a message to a Telegram chat via bot API."""
import os, json, requests
args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
token = args.get("token", os.environ.get("TELEGRAM_SIMON_BATELY", ""))
chat_id = args.get("chat_id", "")
text = args.get("text", "")
if not text: print(json.dumps({"error": "text required"}))
else:
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=10)
        print(json.dumps({"success": r.ok, "message_id": r.json().get("result", {}).get("message_id")}))
    except Exception as e: print(json.dumps({"error": str(e)}))
