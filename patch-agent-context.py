#!/usr/bin/env python3
"""
Baza Empire — Context Patch for core/agent.py
-----------------------------------------------
Surgically patches the existing BazaAgent to add:
  1. ContextMixin wired into __init__
  2. Context-enriched system prompt injected into every LLM call
  3. Journal logging + auto-remember after each message
  4. Auto-summarization every 15 messages

Run from agent-framework-v3/:
    python3 patch-agent-context.py
"""

import re

AGENT_FILE = "core/agent.py"

with open(AGENT_FILE, "r") as f:
    src = f.read()

# ── Backup ────────────────────────────────────────────────────────────────────
with open(AGENT_FILE + ".bak", "w") as f:
    f.write(src)
print("Backup saved to core/agent.py.bak")

# ── Patch 1: Add imports at top ───────────────────────────────────────────────
IMPORT_INJECT = """from core.context_mixin import ContextMixin
from core.context_db import init_context_db, journal_log, save_summary
from core.skills_engine import SkillsEngine
"""

if "from core.context_mixin import ContextMixin" not in src:
    src = src.replace(
        "from core.gpu_pool import gpu_pool",
        "from core.gpu_pool import gpu_pool\n" + IMPORT_INJECT
    )
    print("✓ Patch 1: Imports added")
else:
    print("- Patch 1: Already applied")

# ── Patch 2: Add ContextMixin to class definition ─────────────────────────────
if "class BazaAgent(ContextMixin)" not in src:
    src = src.replace("class BazaAgent:", "class BazaAgent(ContextMixin):")
    print("✓ Patch 2: ContextMixin added to BazaAgent")
else:
    print("- Patch 2: Already applied")

# ── Patch 3: Patch __init__ to init context + message counter ─────────────────
OLD_INIT_END = """        self.redis = redis.Redis(
            host=global_config['redis']['host'],
            port=global_config['redis']['port'],
            decode_responses=True
        )"""

NEW_INIT_END = """        self.redis = redis.Redis(
            host=global_config['redis']['host'],
            port=global_config['redis']['port'],
            decode_responses=True
        )

        # ── Context DB + Skills ───────────────────────────────────────────────
        try:
            init_context_db()
            self.init_context()   # ContextMixin: loads identity, skills engine
            self._msg_counts = {} # chat_id -> message count for auto-summarize
            logger.info(f"[{self.agent_id}] Context DB ready.")
        except Exception as e:
            logger.warning(f"[{self.agent_id}] Context DB unavailable: {e}")
            self._msg_counts = {}"""

if "init_context_db()" not in src:
    src = src.replace(OLD_INIT_END, NEW_INIT_END)
    print("✓ Patch 3: __init__ patched with context init")
else:
    print("- Patch 3: Already applied")

# ── Patch 4: Inject context into system prompt in query_ollama ───────────────
OLD_QUERY = '                    "messages": [{"role": "system", "content": self.system_prompt}] + messages,'

NEW_QUERY = '''                    "messages": [{"role": "system", "content": self._get_enriched_prompt()}] + messages,'''

if "_get_enriched_prompt" not in src:
    src = src.replace(OLD_QUERY, NEW_QUERY)
    print("✓ Patch 4: query_ollama uses enriched prompt")
else:
    print("- Patch 4: Already applied")

# ── Patch 5: Add _get_enriched_prompt method after query_ollama ───────────────
ENRICHED_METHOD = '''
    def _get_enriched_prompt(self) -> str:
        """
        Build the full system prompt for every LLM call:
        base system_prompt + live context (memory, empire knowledge, summaries, skills).
        Falls back to base system_prompt if context DB is unavailable.
        """
        try:
            ctx = self.context()  # from ContextMixin -> context_db.build_agent_context
            if ctx:
                return f"{self.system_prompt}\\n\\n<context>\\n{ctx}\\n</context>"
        except Exception:
            pass
        return self.system_prompt

'''

if "_get_enriched_prompt" not in src:
    # Insert after query_ollama method closing
    src = src.replace(
        "    # ─── Relevance ───",
        ENRICHED_METHOD + "    # ─── Relevance ───"
    )
    print("✓ Patch 5: _get_enriched_prompt method added")
else:
    print("- Patch 5: Already applied")

# ── Patch 6: Journal + auto-remember + auto-summarize in handle_message ───────
# Find the spot right after "clean = strip_name_prefix(...)" and before dispatch check
OLD_AFTER_RESPONSE = '''            clean = strip_name_prefix(response.replace("TASK_COMPLETE", "").strip(), self.name)

            # ── Simon: check for DISPATCH commands ───────────────────────────'''

NEW_AFTER_RESPONSE = '''            clean = strip_name_prefix(response.replace("TASK_COMPLETE", "").strip(), self.name)

            # ── Context: journal + remember + summarize ───────────────────────
            try:
                journal_log(
                    agent_id=self.agent_id,
                    task_type="message",
                    task_description=f"User: {text[:200]}",
                    result=clean[:300],
                    success=True,
                    chat_id=int(chat_id) if chat_id.isdigit() else 0
                )
                # Auto-remember last active chat
                self.remember("last_active_chat_id", chat_id, "session")
                # Track message count and auto-summarize every 15 messages
                self._msg_counts[chat_id] = self._msg_counts.get(chat_id, 0) + 1
                if self._msg_counts[chat_id] % 15 == 0:
                    asyncio.create_task(self._auto_summarize(chat_id, history))
            except Exception as _ctx_err:
                logger.debug(f"Context update skipped: {_ctx_err}")

            # ── Simon: check for DISPATCH commands ───────────────────────────'''

if "_auto_summarize" not in src:
    if OLD_AFTER_RESPONSE in src:
        src = src.replace(OLD_AFTER_RESPONSE, NEW_AFTER_RESPONSE)
        print("✓ Patch 6: journal + auto-remember + auto-summarize added")
    else:
        print("✗ Patch 6: Could not find insertion point — manual patch needed")
else:
    print("- Patch 6: Already applied")

# ── Patch 7: Add _auto_summarize method before run() ─────────────────────────
AUTO_SUMMARIZE_METHOD = '''
    async def _auto_summarize(self, chat_id: str, history: list):
        """Compress recent conversation into a summary and save to context DB."""
        try:
            recent = history[-15:]
            history_text = "\\n".join(
                f"{m['role'].upper()}: {m['content'][:200]}" for m in recent
            )
            summarize_msgs = [{"role": "user", "content":
                f"Summarize this conversation in 2-3 sentences. Focus on facts and decisions:\\n\\n{history_text}"}]
            summary = await self.query_ollama(summarize_msgs)
            if summary and len(summary) > 20:
                save_summary(
                    agent_id=self.agent_id,
                    summary=summary[:500],
                    chat_id=int(chat_id) if chat_id.isdigit() else 0,
                    message_count=len(recent)
                )
                logger.info(f"[{self.agent_id}] Session summarized: {summary[:80]}")
        except Exception as e:
            logger.debug(f"Auto-summarize failed: {e}")

'''

if "_auto_summarize" not in src:
    src = src.replace(
        "    # ─── Start ────",
        AUTO_SUMMARIZE_METHOD + "    # ─── Start ────"
    )
    print("✓ Patch 7: _auto_summarize method added")
else:
    print("- Patch 7: Already applied")

# ── Write patched file ────────────────────────────────────────────────────────
with open(AGENT_FILE, "w") as f:
    f.write(src)

print("\n✅ core/agent.py patched successfully.")
print("Restart agents to apply: sudo systemctl restart baza-agent-*")
