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

from core.context_db import (
    build_agent_context, identity_get,
    memory_set, memory_get, memory_get_all,
    empire_set, empire_get,
    journal_log, save_summary
)
from core.skills_engine import SkillsEngine


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
        """Build full context string for this agent."""
        return build_agent_context(self.agent_id)

    def get_system_prompt(self) -> str:
        """
        Returns the full system prompt for LLM calls.
        Structure: live context (memory/skills) THEN system_prompt last.
        System prompt is placed last so it has final authority over the LLM.
        """
        base = ""
        if self._identity and self._identity.get("system_prompt"):
            base = self._identity["system_prompt"]

        ctx = self.context()
        if ctx:
            return f"<context>\n{ctx}\n</context>\n\n{base}"
        return base

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
