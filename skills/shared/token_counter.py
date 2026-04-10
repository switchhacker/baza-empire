#!/usr/bin/env python3
"""Skill: token_counter — Estimate token count for text.
Usage: ##SKILL:token_counter{"text":"your text here"}##"""
import os, json
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
text = args.get("text","")
chars = len(text)
words = len(text.split())
tokens_est = max(1, chars // 4)
lines = text.count("\n") + 1
print(f"Token Estimate")
print(f"  Characters: {chars:,}")
print(f"  Words: {words:,}")
print(f"  Lines: {lines:,}")
print(f"  Est tokens: ~{tokens_est:,} (chars/4 heuristic)")
print(f"  Cost estimate:")
print(f"    GPT-4o: ~${tokens_est*0.005/1000:.4f}")
print(f"    Claude: ~${tokens_est*0.015/1000:.4f}")
print(f"    Ollama local: $0.00")
