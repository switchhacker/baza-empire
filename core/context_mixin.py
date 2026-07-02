"""
Baza Empire — Context Mixin
-----------------------------
Drop this into any agent to give it full persistent context + skill execution.

Usage in an agent:
    from core.context_mixin import ContextMixin

    class MyAgent(ContextMixin):
        def __init__(self):
            self.agent_id = "claw_batto"
            self.init_context()

        async def handle_message(self, chat_id, text):
            # Build context-enriched system prompt
            system = self.get_system_prompt()

            # Call LLM (your existing ollama call)
            response = await self.ollama_chat(system, text, chat_id)

            # Parse and execute any skills the LLM requested
            response, skill_results = self.skills.parse_and_run(response, chat_id)

            # Save this exchange to memory
            self.remember(f"last_message_from_{chat_id}", text[:100])

            return response
"""

import json
import os

from core.context_db import (
    build_agent_context, identity_get,
    memory_set, memory_get, memory_get_all,
    empire_set, empire_get,
    journal_log, save_summary
)
from core.skills_engine import SkillsEngine


# Repo root: agent-framework-v3/
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PERSONA_SECTIONS = ("IDENTITY.md", "SOUL.md", "MISSION.md", "USER.md")
_PERSONA_CACHE: dict = {}   # agent_id → (mtime_sum, content)

TELEGRAM_STYLE = """
## Telegram formatting (house style)
Your replies render in Telegram with rich text. Write normal markdown — it is converted automatically.
- Simple answers: 1-3 plain sentences. Do NOT force structure onto chit-chat.
- Structured answers: start with one short **bold** header line.
- Status marks: ✅ done · ⚠️ needs attention · ❌ failed · ☐ todo.
- Use "- " bullets for lists and "- [x] / - [ ]" checklists for multi-step work.
- Put file paths, commands, and service names in `backticks`.
- No tables, no nested headers. Keep messages compact.
"""


def _load_persona_files(agent_id: str) -> str:
    """Load and concatenate persona/*.md files for an agent.
    Returns "" if no MISSION.md exists (signal that this agent hasn't migrated yet).
    Cached by mtime so live edits are picked up without restart.
    """
    persona_dir = os.path.join(_REPO_ROOT, "agents", agent_id, "persona")
    mission_path = os.path.join(persona_dir, "MISSION.md")
    if not os.path.isfile(mission_path):
        return ""

    # Cache key = sum of mtimes for present files (cheap change detection)
    paths = [os.path.join(persona_dir, f) for f in _PERSONA_SECTIONS]
    mtime_sum = sum(os.path.getmtime(p) for p in paths if os.path.isfile(p))
    cached = _PERSONA_CACHE.get(agent_id)
    if cached and cached[0] == mtime_sum:
        return cached[1]

    parts = []
    for fname in _PERSONA_SECTIONS:
        fpath = os.path.join(persona_dir, fname)
        if os.path.isfile(fpath):
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    parts.append(f.read().strip())
            except Exception:
                pass
    content = "\n\n".join(parts)
    _PERSONA_CACHE[agent_id] = (mtime_sum, content)
    return content


class ContextMixin:
    """
    Mixin that gives any agent:
      - self.skills       → SkillsEngine instance
      - self.context()    → full context string for LLM injection
      - self.remember()   → persist a memory fact
      - self.recall()     → retrieve a memory fact
      - self.journal()    → log an action to task journal
      - self.summarize()  → save a session summary
    """

    def init_context(self):
        """Call this in __init__ after setting self.agent_id."""
        self.skills = SkillsEngine(self.agent_id)
        self._identity = identity_get(self.agent_id)

    def context(self) -> str:
        """Build full context string for this agent, including recent events.

        Empire-state header is PREPENDED here so every agent — both BaseAgent
        and legacy BazaAgent — sees current LIVE/KILLED/RECENT plus their own
        identity, skills, and team-online roster on every LLM call. Cached
        with the rest of the system_prompt at the caller level.
        """
        empire_header = ""
        try:
            from core import empire_state
            empire_header = empire_state.build_header(self.agent_id)
        except Exception:
            pass

        ctx = build_agent_context(self.agent_id)
        # Append recent cross-agent events from the event bus
        try:
            from core.event_bus import get_recent_events_sync
            recent_events = get_recent_events_sync(self.agent_id, limit=10)
            if recent_events:
                ctx += "\n\n## Recent Agent Events\n"
                for e in recent_events[:5]:
                    ctx += f"- [{e.source}] {e.type}: {json.dumps(e.data)[:200]}\n"
        except Exception:
            pass
        if empire_header:
            ctx = empire_header + ("\n\n" + ctx if ctx else "")
        return ctx

    def get_system_prompt(self) -> str:
        """
        Returns the full system prompt for LLM calls.
        Structure: live context (memory/skills) THEN system_prompt last.
        System prompt is placed last so it has final authority over the LLM.

        Persona resolution chain (first hit wins):
          1. agents/<id>/persona/{IDENTITY,SOUL,MISSION,USER}.md   ← Graft 1, preferred
          2. agent_identity.system_prompt (PostgreSQL)
          3. (caller's class-level fallback)
        """
        base = _load_persona_files(self.agent_id)
        if not base and self._identity and self._identity.get("system_prompt"):
            base = self._identity["system_prompt"]

        ctx = self.context()
        if ctx:
            return f"<context>\n{ctx}\n</context>\n\n{base}{TELEGRAM_STYLE}"
        return base + TELEGRAM_STYLE

    def remember(self, key: str, value: str, category: str = "general"):
        """Persist a memory fact."""
        memory_set(self.agent_id, key, value, category)

    def recall(self, key: str) -> str:
        """Retrieve a memory fact."""
        return memory_get(self.agent_id, key)

    def recall_all(self, category: str = None) -> dict:
        """Retrieve all memory facts."""
        return memory_get_all(self.agent_id, category)

    def journal(self, task_type: str, description: str,
                result: str = None, success: bool = True,
                input_data: dict = {}, chat_id: int = None):
        """Log an action to the task journal."""
        journal_log(
            agent_id=self.agent_id,
            task_type=task_type,
            task_description=description,
            result=result,
            success=success,
            input_data=input_data,
            chat_id=chat_id
        )

    def summarize(self, summary: str, chat_id: int = None, message_count: int = 0):
        """Save a compressed session summary."""
        save_summary(
            agent_id=self.agent_id,
            summary=summary,
            chat_id=chat_id,
            message_count=message_count
        )

    def run_skill(self, skill_name: str, args: dict = {}, chat_id: int = None) -> dict:
        """Directly invoke a skill."""
        return self.skills.run(skill_name, args, chat_id=chat_id)
