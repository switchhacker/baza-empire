#!/usr/bin/env python3
"""Debug: print exactly what Simon sends to Ollama."""
import sys
sys.path.insert(0, ".")
from core.context_mixin import ContextMixin
from core.context_db import identity_set, identity_get

class FakeSimon(ContextMixin):
    def __init__(self):
        self.agent_id = "simon_bately"
        self.init_context()

s = FakeSimon()
prompt = s.get_system_prompt()
print(f"=== TOTAL PROMPT LENGTH: {len(prompt)} chars ===\n")
print(prompt)
