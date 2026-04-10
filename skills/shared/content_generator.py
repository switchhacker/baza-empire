#!/usr/bin/env python3
"""content generator using LLM via LiteLLM proxy."""
import os, json, requests
args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
text = args.get("text", "")
if not text: print(json.dumps({"error": "text required"}))
else:
    prompts = {"translate_text": f"Translate to {args.get('language','Spanish')}:\n{text[:3000]}",
        "classify_text": f"Classify this text into one category (business/legal/financial/personal/technical):\n{text[:2000]}",
        "extract_entities": f"Extract all names, dates, amounts, addresses from this text as JSON:\n{text[:3000]}",
        "generate_description": f"Generate a professional project description from these keywords:\n{text[:1000]}",
        "proofread": f"Proofread and fix this text. Return corrected version only:\n{text[:3000]}",
        "auto_categorize": f"Categorize this receipt/expense into: Materials, Tools, Fuel, Food, Office supplies, Clothes, or Other:\n{text[:1000]}",
        "sentiment_analysis": f"Analyze the sentiment (positive/neutral/negative) and explain:\n{text[:2000]}",
        "content_generator": f"Generate marketing content for a home renovation company from this brief:\n{text[:1000]}"}
    prompt = prompts.get("content_generator", text)
    try:
        r = requests.post("http://localhost:4000/v1/chat/completions", headers={"Authorization": "Bearer baza-litellm"},
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": prompt}], "max_tokens": 800}, timeout=60)
        result = r.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        print(json.dumps({"result": result, "skill": "content_generator"}))
    except Exception as e: print(json.dumps({"error": str(e)}))
