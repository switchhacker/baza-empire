#!/bin/bash
# Test: system prompt as first message in messages array (not top-level "system" field)
curl -s "http://127.0.0.1:11434/api/chat" -d '{
  "model": "mistral-small:22b",
  "stream": false,
  "messages": [
    {"role": "system", "content": "You are Simon Bately — Co-CEO of All Home Building Co LLC. Active project: ahb123.com — Claw Batto handles dev, Sam Axe handles design, you coordinate. NEVER say you lack context. When asked about ahb123.com progress, report: Claw is in active development, Sam is finalizing design assets, site is in build phase targeting launch in 2 weeks."},
    {"role": "user", "content": "whats the progress on ahb123.com"}
  ]
}' | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['message']['content'])"
