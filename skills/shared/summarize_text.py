#!/usr/bin/env python3
"""Summarize long text using LLM."""
import os, json, requests
args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
text = args.get("text", "")
if not text: print(json.dumps({"error": "text required"}))
else:
    try:
        r = requests.post("http://localhost:4000/v1/chat/completions", headers={"Authorization": "Bearer baza-litellm"}, json={"model": "gpt-4o", "messages": [{"role": "user", "content": f"Summarize this concisely:\n\n{text[:4000]}"}], "max_tokens": 500}, timeout=60)
        summary = r.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        print(json.dumps({"summary": summary, "original_length": len(text)}))
    except Exception as e: print(json.dumps({"error": str(e)}))
