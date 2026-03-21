"""
GPU Pool — manages two Ollama instances across AMD (Vulkan) and NVIDIA (CUDA).
Agents acquire a GPU slot, use it, then release it so the next agent can go.
"""

import threading
import time
from dataclasses import dataclass, field
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
    """
    Two-slot GPU pool. Agents call acquire() to get a free GPU,
    then release() when done. If both are busy, waits until one frees up.
    """

    def __init__(self):
        self.slots = [
            GPUSlot(id=0, url="http://localhost:11434", name="AMD RX 6700 XT (Vulkan)"),
            GPUSlot(id=1, url="http://localhost:11435", name="NVIDIA RTX 3070 (CUDA)"),
        ]
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)

    def acquire(self, agent_id: str, timeout: float = 120.0) -> Optional[GPUSlot]:
        """
        Block until a GPU slot is free, then claim it.
        Returns the slot, or None if timeout exceeded.
        """
        deadline = time.time() + timeout
        with self._condition:
            while True:
                # Find a free slot
                for slot in self.slots:
                    if not slot.in_use:
                        slot.in_use = True
                        slot.current_agent = agent_id
                        slot.acquired_at = time.time()
                        print(f"[GPU Pool] {agent_id} acquired {slot.name}")
                        return slot

                # All busy — wait
                remaining = deadline - time.time()
                if remaining <= 0:
                    print(f"[GPU Pool] {agent_id} timed out waiting for GPU")
                    return None
                self._condition.wait(timeout=min(remaining, 2.0))

    def release(self, slot: GPUSlot):
        """Release a GPU slot back to the pool."""
        with self._condition:
            elapsed = time.time() - (slot.acquired_at or time.time())
            print(f"[GPU Pool] {slot.current_agent} released {slot.name} after {elapsed:.1f}s")
            slot.in_use = False
            slot.current_agent = None
            slot.acquired_at = None
            self._condition.notify_all()

    def status(self) -> list:
        """Return current status of all slots."""
        with self._lock:
            return [
                {
                    "id": s.id,
                    "name": s.name,
                    "in_use": s.in_use,
                    "agent": s.current_agent,
                }
                for s in self.slots
            ]

# Singleton — shared across all agents
gpu_pool = GPUPool()
