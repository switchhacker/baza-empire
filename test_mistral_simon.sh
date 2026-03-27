#!/bin/bash
curl -s "http://127.0.0.1:11434/api/chat" -d '{
  "model": "mistral-small:22b",
  "stream": false,
  "system": "You are Simon Bately — Co-CEO of All Home Building Co LLC. Active project: ahb123.com — Claw Batto handles dev/deployment, Sam Axe handles design, you coordinate. NEVER say you lack context about your team or projects. Answer from what you know.",
  "messages": [
    {"role": "user", "content": "whats the progress on ahb123.com"}
  ]
}' | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['message']['content'])"
