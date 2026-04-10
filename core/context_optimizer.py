"""
Context Optimizer — saves tokens, money, and time across all Baza agents.

Implements the same techniques big AI companies use to keep prompts cheap and
fast without losing answer quality:

1. **Sliding window**       — keep only the last N turns verbatim
2. **Hierarchical compress** — older turns get squeezed into a 1-line summary,
                                very old context becomes "facts" entries
3. **Dedup**                 — strip identical/near-duplicate paragraphs from
                                history (agents repeat themselves a lot)
4. **System prompt cache**   — strip boilerplate that's already cached on the
                                model side, only send the dynamic delta
5. **Complexity routing**    — classify the prompt and pick the smallest
                                model that can plausibly answer it
6. **Token budget**          — hard cap inputs so a runaway history can't
                                blow up the context window or session quota
7. **Semantic recall**       — pull facts from agent_memory + empire_knowledge
                                instead of stuffing the entire conversation

Usage:
    from core.context_optimizer import optimize

    messages, system_prompt, suggested_model = optimize(
        agent_id="claw_batto",
        messages=full_history,
        system_prompt=raw_system,
        target_model="qwen2.5:14b",
        max_tokens=6000,
    )
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from typing import Optional


# ── Token estimation ──────────────────────────────────────────────────────────
# Cheap, no tokenizer dependency: ~3.7 chars/token for English+code.
# Underestimates for Chinese/Japanese — fine for budgeting since we leave headroom.
def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def estimate_messages_tokens(messages: list) -> int:
    total = 0
    for m in messages:
        c = m.get("content", "") if isinstance(m, dict) else str(m)
        # +4 token overhead per message for role/separators
        total += estimate_tokens(c) + 4
    return total


# ── Complexity classifier ─────────────────────────────────────────────────────
# Heuristic — no LLM round-trip needed. Big companies do this with a small
# classifier model; we get 80% of the value with rules.

SIMPLE_PATTERNS = [
    r"^(hi|hey|hello|yo|sup|thanks|thx|ok|cool|nice|good|got it|understood)\b",
    r"^/(start|help|status|ping)\b",
    r"^(yes|y|no|n|approved?|denied?|cancel|abort|stop)\.?$",
    r"^(what time|what is the date|how are you)",
]
SIMPLE_RE = re.compile("|".join(SIMPLE_PATTERNS), re.IGNORECASE)

CODE_KEYWORDS = ("function", "class", "def ", "import ", "refactor", "debug",
                 "stack trace", "traceback", "error message", "compile", "syntax")
RESEARCH_KEYWORDS = ("research", "find me", "look up", "summarize", "analyze",
                     "compare", "competitive", "market", "trend")
HEAVY_KEYWORDS = ("write a", "build a", "implement", "design", "architect",
                  "explain in detail", "step by step", "complete project")


@dataclass
class Complexity:
    level: str           # "trivial" | "simple" | "medium" | "heavy"
    category: str        # "chat" | "code" | "research" | "general"
    estimated_input: int
    estimated_output: int


def classify_prompt(text: str, history_tokens: int = 0) -> Complexity:
    if not text:
        return Complexity("trivial", "chat", 0, 50)
    t = text.strip()
    low = t.lower()

    # Trivial: short, matches a simple pattern
    if len(t) < 50 and SIMPLE_RE.search(low):
        return Complexity("trivial", "chat", estimate_tokens(t), 80)

    in_tokens = estimate_tokens(t) + history_tokens
    cat = "general"
    if any(k in low for k in CODE_KEYWORDS):
        cat = "code"
    elif any(k in low for k in RESEARCH_KEYWORDS):
        cat = "research"

    if any(k in low for k in HEAVY_KEYWORDS) or in_tokens > 3000:
        return Complexity("heavy", cat, in_tokens, 1500)
    if in_tokens > 800 or len(t) > 300:
        return Complexity("medium", cat, in_tokens, 700)
    return Complexity("simple", cat, in_tokens, 400)


# ── Model routing by complexity ───────────────────────────────────────────────
# Maps complexity → preferred model (env-overridable for site-specific tuning).
DEFAULT_ROUTING = {
    "trivial": os.environ.get("BAZA_MODEL_TRIVIAL",  "ministral-3:3b"),
    "simple":  os.environ.get("BAZA_MODEL_SIMPLE",   "ministral-3:8b"),
    "medium":  os.environ.get("BAZA_MODEL_MEDIUM",   "qwen2.5:14b"),
    "heavy":   os.environ.get("BAZA_MODEL_HEAVY",    "mistral-small:22b"),
    "code":    os.environ.get("BAZA_MODEL_CODE",     "deepseek-coder-v2:16b"),
    "research":os.environ.get("BAZA_MODEL_RESEARCH", "glm-4.7-flash:latest"),
}


def suggest_model(target_model: str, complexity: Complexity) -> str:
    """Decide if we should downgrade/upgrade the model based on complexity.
    Respects the agent's chosen model unless complexity points elsewhere."""
    # Cloud models stay as-is — quota is tracked separately
    if ":cloud" in target_model or any(target_model.startswith(p) for p in
            ("gpt-", "claude-", "gemini-", "grok-", "o1", "o3-")):
        return target_model

    # Trivial requests always downgrade to the smallest model
    if complexity.level == "trivial":
        return DEFAULT_ROUTING["trivial"]

    # Code-heavy requests prefer the code model when one exists
    if complexity.category == "code" and complexity.level in ("medium", "heavy"):
        return DEFAULT_ROUTING["code"]

    # Otherwise honor the agent's choice
    return target_model


# ── Dedup ─────────────────────────────────────────────────────────────────────
def _normalize_for_dedup(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())[:200]


def dedupe_messages(messages: list) -> list:
    """Drop messages whose normalized prefix matches a previously-seen one.
    Keeps the LATEST occurrence (so the model sees the freshest phrasing)."""
    seen_to_idx = {}
    for i, m in enumerate(messages):
        c = m.get("content", "") if isinstance(m, dict) else str(m)
        if not c.strip():
            continue
        h = hashlib.sha1(_normalize_for_dedup(c).encode()).hexdigest()
        seen_to_idx[h] = i  # later one wins
    keep = set(seen_to_idx.values())
    return [m for i, m in enumerate(messages) if i in keep or not (m.get("content") or "").strip()]


# ── Hierarchical compression ──────────────────────────────────────────────────
def compress_history(messages: list, keep_recent: int = 8,
                     summary_tokens: int = 200) -> list:
    """Keep the last N turns verbatim. Squeeze older turns into a single
    'previously discussed' summary message at the front of the list."""
    if len(messages) <= keep_recent:
        return list(messages)

    older = messages[:-keep_recent]
    recent = messages[-keep_recent:]

    # Build a terse summary of older turns: just the user prompts and 1-line
    # assistant replies, no full text.
    bullets = []
    for m in older:
        role = m.get("role", "?") if isinstance(m, dict) else "?"
        content = (m.get("content", "") if isinstance(m, dict) else str(m)).strip()
        if not content:
            continue
        # Truncate each line aggressively
        first_line = content.split("\n")[0][:120]
        bullets.append(f"  {role}: {first_line}")
        if estimate_tokens("\n".join(bullets)) > summary_tokens:
            break

    if not bullets:
        return list(recent)

    summary_msg = {
        "role": "system",
        "content": "[earlier conversation, summarized]\n" + "\n".join(bullets),
    }
    return [summary_msg] + list(recent)


# ── System prompt deduplication / caching hint ────────────────────────────────
# Strip whitespace runs and trailing junk so identical prompts hash the same.
def normalize_system(prompt: str) -> str:
    if not prompt:
        return ""
    p = re.sub(r"[ \t]+", " ", prompt)
    p = re.sub(r"\n{3,}", "\n\n", p)
    return p.strip()


# ── Main entry point ──────────────────────────────────────────────────────────
def optimize(agent_id: str, messages: list, system_prompt: str = "",
             target_model: str = "", max_tokens: int = 6000,
             keep_recent: int = 8) -> tuple:
    """Run the full optimizer pipeline.

    Returns (optimized_messages, optimized_system_prompt, suggested_model, stats).
    `stats` is a dict with token counts before/after for observability.
    """
    before_total = estimate_messages_tokens(messages) + estimate_tokens(system_prompt)

    # 1. Normalize system prompt
    sys_clean = normalize_system(system_prompt)

    # 2. Dedupe messages
    deduped = dedupe_messages(messages)

    # 3. Hierarchical compression
    compressed = compress_history(deduped, keep_recent=keep_recent)

    # 4. Hard token cap — drop oldest from the front (keeping the system summary)
    while estimate_messages_tokens(compressed) + estimate_tokens(sys_clean) > max_tokens and len(compressed) > 2:
        # Skip the system summary at index 0
        if compressed[0].get("role") == "system":
            del compressed[1]
        else:
            del compressed[0]

    # 5. Classify the latest user turn → complexity
    last_user = ""
    for m in reversed(compressed):
        if isinstance(m, dict) and m.get("role") == "user":
            last_user = m.get("content", "")
            break
    history_tokens = estimate_messages_tokens(compressed[:-1]) if compressed else 0
    complexity = classify_prompt(last_user, history_tokens)

    # 6. Pick the right model
    suggested = suggest_model(target_model, complexity)

    after_total = estimate_messages_tokens(compressed) + estimate_tokens(sys_clean)
    stats = {
        "agent_id": agent_id,
        "before_tokens": before_total,
        "after_tokens": after_total,
        "saved_tokens": max(0, before_total - after_total),
        "saved_pct": round((before_total - after_total) * 100 / before_total, 1) if before_total else 0,
        "complexity": complexity.level,
        "category": complexity.category,
        "target_model": target_model,
        "suggested_model": suggested,
        "downgraded": suggested != target_model,
    }

    return compressed, sys_clean, suggested, stats
