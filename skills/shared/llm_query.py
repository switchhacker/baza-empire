#!/usr/bin/env python3
"""Query local Ollama or LiteLLM with a prompt."""
import os, json, requests
args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
prompt = args.get("prompt", "")
model = args.get("model", "gpt-4o")
base = args.get("base_url", "http://localhost:4000/v1")
key = args.get("api_key", "baza-litellm")
if not prompt: print(json.dumps({"error": "prompt required"}))
else:
    try:
        r = requests.post(f"{base}/chat/completions", headers={"Authorization": f"Bearer {key}"}, json={"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 1000}, timeout=60)
        resp = r.json(); content = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
        print(json.dumps({"response": content, "model": model}))
    except Exception as e: print(json.dumps({"error": str(e)}))
