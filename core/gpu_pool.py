"""
GPU Pool — manages two Ollama instances across AMD (11434) and NVIDIA (11435).
Agents acquire a free slot, run inference, then release it for the next agent.
"""

import threading
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class GPUSlot:
    id: int
    url: str
    name: str
    in_use: bool = False
    current_agent: Optional[str] = None
    acquired_at: Optional[float] = None


class GPUPool:
    def __init__(self):
        self.slots = [
            GPUSlot(id=0, url="http://127.0.0.1:11434", name="AMD RX 6700 XT"),
            GPUSlot(id=1, url="http://127.0.0.1:11435", name="NVIDIA RTX 3070"),
        ]
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)

    def acquire(self, agent_id: str, timeout: float = 120.0) -> Optional[GPUSlot]:
        deadline = time.time() + timeout
        with self._condition:
            while True:
                for slot in self.slots:
                    if not slot.in_use:
                        slot.in_use = True
                        slot.current_agent = agent_id
                        slot.acquired_at = time.time()
                        print(f"[GPU Pool] {agent_id} acquired {slot.name} ({slot.url})")
                        return slot
                remaining = deadline - time.time()
                if remaining <= 0:
                    print(f"[GPU Pool] {agent_id} timed out waiting for a free GPU")
                    return None
                self._condition.wait(timeout=min(remaining, 2.0))

    def release(self, slot: GPUSlot):
        with self._condition:
            elapsed = time.time() - (slot.acquired_at or time.time())
            print(f"[GPU Pool] {slot.current_agent} released {slot.name} after {elapsed:.1f}s")
            slot.in_use = False
            slot.current_agent = None
            slot.acquired_at = None
            self._condition.notify_all()

    def status(self) -> list:
        with self._lock:
            return [
                {"id": s.id, "name": s.name, "url": s.url,
                 "in_use": s.in_use, "agent": s.current_agent}
                for s in self.slots
            ]


gpu_pool = GPUPool()
