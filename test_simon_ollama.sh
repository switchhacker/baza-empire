#!/bin/bash
# Direct Ollama test — bypasses all agent code
# Tests if the model actually follows the system prompt

OLLAMA_URL="http://127.0.0.1:11434"

curl -s "$OLLAMA_URL/api/chat" -d '{
  "model": "qwen2.5:14b",
  "stream": false,
  "system": "You are Simon Bately — Co-CEO of All Home Building Co LLC. You work with a team: Claw Batto (Dev/DevOps), Phil Hass (Legal/Finance), Sam Axe (Creative). Active project: ahb123.com — Claw handles dev, Sam handles design, you coordinate. NEVER say you lack context about your team or projects. Answer from what you know.",
  "messages": [
    {"role": "user", "content": "whats the progress on ahb123.com"}
  ]
}' | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['message']['content'])"
