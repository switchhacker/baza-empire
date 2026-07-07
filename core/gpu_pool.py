"""
GPU Pool — LLM inference slots:
  - 11434  AMD RX 6700 XT   (Vulkan, 12GB) — primary Vulkan
  - 11435  NVIDIA RTX 3070  (CUDA, budget-capped) — small models only; card is
           shared with SD WebUI imaging (see NVIDIA_LLM_BUDGET_MB)
  - 11437  AMD RX 6700 XT   (Vulkan, 12GB) — secondary Vulkan (overflow,
           same physical card as 11434 — extra queue, not extra VRAM)
  - 11436  CPU + 64GB RAM   (no GPU)       — big-model fallback

Agents acquire the BEST slot for their model (size + temperature + load
aware), run inference, then release. Temperature awareness skips a GPU that
crossed the warning threshold and steers traffic to the cooler one.
"""

import os
import re
import threading
import time
import subprocess
import requests
from dataclasses import dataclass, field
from typing import Optional

# Quant-accurate model sizing: actual on-disk size from ollama /api/tags beats
# guessing VRAM from parameter count (a 26B model is ~17GB at Q4 but ~10GB at
# Q2). Cached briefly so routing stays cheap. Disk size + overhead = footprint.
_OLLAMA_SIZE_CACHE: dict = {}
_OLLAMA_SIZE_CACHE_TS: float = 0.0
_OLLAMA_SIZE_TTL = 300.0
_FOOTPRINT_OVERHEAD_MB = 1500   # activation + KV cache (matches MODEL_FOOTPRINT_MB)

# Per-instance liveness, cached so routing stays cheap. The CUDA instance
# (ollama-cuda.service) is optional — if it's stopped, its slot must be
# skipped rather than routed to a dead port.
_INSTANCE_UP_CACHE: dict = {}
_INSTANCE_UP_TTL = 30.0


def _instance_up(url: str) -> bool:
    now = time.time()
    cached = _INSTANCE_UP_CACHE.get(url)
    if cached and (now - cached[1]) < _INSTANCE_UP_TTL:
        return cached[0]
    try:
        up = requests.get(f"{url}/api/version", timeout=1.5).ok
    except Exception:
        up = False
    _INSTANCE_UP_CACHE[url] = (up, now)
    return up


# ── Backend definitions ───────────────────────────────────────────────────────
AMD_URL     = "http://127.0.0.1:11434"
NVIDIA_URL  = "http://127.0.0.1:11435"
CPU_URL     = "http://127.0.0.1:11436"
AMD2_URL    = "http://127.0.0.1:11437"

# VRAM budgets (rough — used to decide which slot CAN host a given model)
NVIDIA_VRAM_MB = 8192    # RTX 3070
AMD_VRAM_MB    = 12288   # RX 6700 XT
# The 3070 is shared with SD WebUI (--always-offload-from-vram, so it idles
# near-empty but reclaims ~5-6GB during generation). Only let small models
# (glm-ocr, minicpm — the receipt-OCR pair) route there, keeping the rest of
# the card free for imaging bursts. Raise toward NVIDIA_VRAM_MB only if SD
# WebUI is retired from the 3070.
NVIDIA_LLM_BUDGET_MB = 4200

# Approximate model footprint in MB at the quantization on disk.
# Add a model here if you want explicit routing; otherwise the size is
# inferred from /api/show on first use.
# Disk size + ~1.5GB activation/KV cache overhead = real VRAM footprint.
MODEL_FOOTPRINT_MB = {
    "ministral-3:3b":          4500,    # 3.0 + 1.5
    "gemma3:4b":               4800,    # 3.3 + 1.5
    "llama3.1:8b":             6400,    # 4.9 + 1.5
    "ministral-3:8b":          7500,    # 6.0 + 1.5
    "qwen3-vl:latest":         7600,    # 6.1 + 1.5
    "qwen3.5:latest":          8100,    # 6.6 + 1.5
    "minicpm-v4.6:latest":     3100,    # 1.6 + 1.5
    "glm-ocr:latest":          3700,    # 2.2 + 1.5
    "lfm2.5:8b":               6700,    # 5.2 + 1.5
    "gemma4:12b-it-qat":       8700,    # 7.2 + 1.5
    "llava:13b":               9500,    # 8.0 + 1.5
    "gemma3:12b":              9600,    # 8.1 + 1.5
    "deepseek-coder-v2:16b":   10400,   # 8.9 + 1.5
    "qwen2.5:14b":             10500,   # 9.0 + 1.5
    "ministral-3:14b":         10600,   # 9.1 + 1.5
    "mistral-small:22b":       13500,   # 12 + 1.5
    "gpt-oss:20b":             14500,   # 13 + 1.5
    "gemma4:26b-a4b-it-qat":   16500,   # 15 + 1.5 — MoE 4B active, CPU-friendly
    "qwen3.6:27b":             18500,   # 17 + 1.5 — dense, slow on CPU
    "glm-4.7-flash:latest":    20500,   # 19 + 1.5
    "nemotron-cascade-2:30b":  25500,   # 24 + 1.5 — MoE 3B active, CPU-friendly
    "nemotron-3-nano:latest":  25500,   # 24 + 1.5
    "qwen3-coder-next:latest": 52500,   # CPU only
    "nemotron-3-super:latest": 87500,   # CPU only
}

# Temperature thresholds — pool refuses a GPU above WARN, prefers cooler GPU.
NVIDIA_TEMP_WARN = 80   # °C — RTX 3070 starts throttling around 83
NVIDIA_TEMP_CRIT = 87
AMD_TEMP_WARN    = 90   # RX 6700 XT junction is fine up to 110
AMD_TEMP_CRIT    = 100


def _read_nvidia_temp() -> Optional[int]:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=3,
        )
        if out.returncode == 0:
            return int(out.stdout.strip().split("\n")[0])
    except Exception:
        pass
    return None


def _read_amd_temp() -> Optional[int]:
    """Read AMD junction temp from amdgpu hwmon."""
    try:
        for h in os.listdir("/sys/class/hwmon"):
            base = f"/sys/class/hwmon/{h}"
            try:
                with open(f"{base}/name") as f:
                    if f.read().strip() != "amdgpu":
                        continue
            except Exception:
                continue
            for tf in ("temp2_input", "temp1_input"):  # junction first, edge fallback
                tp = f"{base}/{tf}"
                if os.path.exists(tp):
                    try:
                        with open(tp) as f:
                            v = int(f.read().strip())
                            if v > 0:
                                return v // 1000
                    except Exception:
                        pass
    except Exception:
        pass
    return None


# ── Slot ──────────────────────────────────────────────────────────────────────
@dataclass
class GPUSlot:
    id: int
    url: str
    name: str
    backend: str         # "cuda" | "vulkan" | "cpu"
    vram_mb: int         # 0 for CPU
    in_use: bool = False
    current_agent: Optional[str] = None
    acquired_at: Optional[float] = None
    last_temp: Optional[int] = None
    temp_warn: int = 999
    temp_crit: int = 999

    def read_temp(self) -> Optional[int]:
        if self.backend == "cuda":
            self.last_temp = _read_nvidia_temp()
        elif self.backend == "vulkan":
            self.last_temp = _read_amd_temp()
        else:
            self.last_temp = None
        return self.last_temp


# ── Pool ──────────────────────────────────────────────────────────────────────
class GPUPool:
    def __init__(self):
        # Routing prefers the smallest GPU that fits, so with the NVIDIA slot
        # capped at NVIDIA_LLM_BUDGET_MB only small models (receipt-OCR pair:
        # glm-ocr, minicpm-v4.6) land on the 3070 (CUDA, fastest) while
        # everything 8-11GB stays on AMD (Vulkan, 12GB). CPU is last resort.
        # 2026-07-07: NVIDIA slot re-added with a small budget — SD WebUI still
        # owns the 3070 for imaging (--always-offload-from-vram keeps it empty
        # when idle), but the card was sitting at 6% util while the AMD card
        # thrashed through 96 model loads/48h. Requires ollama-cuda.service
        # (:11435) to be running.
        self.slots = [
            GPUSlot(id=0, url=AMD_URL, name="AMD RX 6700 XT (Vulkan)",
                    backend="vulkan", vram_mb=AMD_VRAM_MB,
                    temp_warn=AMD_TEMP_WARN, temp_crit=AMD_TEMP_CRIT),
            GPUSlot(id=1, url=NVIDIA_URL, name="NVIDIA RTX 3070 (CUDA, small models)",
                    backend="cuda", vram_mb=NVIDIA_LLM_BUDGET_MB,
                    temp_warn=NVIDIA_TEMP_WARN, temp_crit=NVIDIA_TEMP_CRIT),
            GPUSlot(id=2, url=AMD2_URL, name="AMD RX 6700 XT (Vulkan) #2",
                    backend="vulkan", vram_mb=AMD_VRAM_MB,
                    temp_warn=AMD_TEMP_WARN, temp_crit=AMD_TEMP_CRIT),
            GPUSlot(id=3, url=CPU_URL, name="CPU + 64GB RAM",
                    backend="cpu",    vram_mb=0),
        ]
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)

    # ── Routing logic ─────────────────────────────────────────────────────────
    def _refresh_ollama_sizes(self):
        """Populate the model→footprint cache from the AMD ollama instances'
        actual pulled-model sizes. Best-effort; failures leave the cache as-is."""
        global _OLLAMA_SIZE_CACHE, _OLLAMA_SIZE_CACHE_TS
        now = time.time()
        if _OLLAMA_SIZE_CACHE and (now - _OLLAMA_SIZE_CACHE_TS) < _OLLAMA_SIZE_TTL:
            return
        sizes = {}
        for url in (AMD_URL, AMD2_URL):
            try:
                r = requests.get(f"{url}/api/tags", timeout=2)
                for m in r.json().get("models", []):
                    name, sz = m.get("name"), m.get("size")
                    if name and sz:
                        sizes[name] = int(sz / 1024 / 1024) + _FOOTPRINT_OVERHEAD_MB
            except Exception:
                continue
        if sizes:
            _OLLAMA_SIZE_CACHE = sizes
            _OLLAMA_SIZE_CACHE_TS = now

    def _model_size_mb(self, model: str) -> int:
        """Best guess at model VRAM footprint, quant-accurate when possible."""
        if not model:
            return 0
        m = model.strip()
        # 1. Explicit override always wins.
        if m in MODEL_FOOTPRINT_MB:
            return MODEL_FOOTPRINT_MB[m]
        # 2. Actual on-disk size from ollama (quant-accurate) beats the
        #    parameter-count heuristic — a 26B Q2 fits the 12GB card; a 26B Q4
        #    doesn't, and only the real size can tell them apart.
        self._refresh_ollama_sizes()
        if m in _OLLAMA_SIZE_CACHE:
            return _OLLAMA_SIZE_CACHE[m]
        # 3. Try without :latest
        base = m.split(":")[0]
        if base in MODEL_FOOTPRINT_MB:
            return MODEL_FOOTPRINT_MB[base]
        # 4. Heuristic from name (e.g. "qwen2.5:14b" → 14B → ~9000MB)
        match = re.search(r"(\d+)b", m.lower())
        if match:
            params = int(match.group(1))
            return params * 650  # ~0.65 GB/B at Q4
        return 0

    def _slots_capable(self, model: str) -> list:
        """Return slots that can host the model, ordered by preference
        (smallest GPU that fits → cooler GPU → CPU as last resort)."""
        size = self._model_size_mb(model)
        capable = []
        for s in self.slots:
            if s.backend == "cpu":
                capable.append(s)  # CPU always works
                continue
            # Skip slots whose Ollama instance isn't running (ollama-cuda
            # is optional and may be stopped)
            if not _instance_up(s.url):
                continue
            # Refuse GPU above critical temp
            t = s.read_temp()
            if t is not None and t >= s.temp_crit:
                continue
            # Skip GPUs that obviously can't fit (small slop for KV cache slack)
            if size > 0 and size > s.vram_mb + 800:
                continue
            capable.append(s)

        def score(s):
            # Score = (cpu_last, warn_penalty, vram_asc)
            # - CPU is always last resort
            # - GPUs over warn temperature get a big penalty (so the cooler GPU wins)
            # - Prefer the SMALLEST GPU that still fits (saves the big GPU for big models)
            if s.backend == "cpu":
                return (10, 0, 0)
            t = s.last_temp or 0
            warn_penalty = 5 if (t and t >= s.temp_warn) else 0
            return (0, warn_penalty, s.vram_mb)

        capable.sort(key=score)
        return capable

    # ── Acquire / release ─────────────────────────────────────────────────────
    def acquire(self, agent_id: str, timeout: float = 120.0,
                model: str = "") -> Optional[GPUSlot]:
        deadline = time.time() + timeout
        with self._condition:
            while True:
                preferred = self._slots_capable(model)
                for slot in preferred:
                    if not slot.in_use:
                        slot.in_use = True
                        slot.current_agent = agent_id
                        slot.acquired_at = time.time()
                        temp = f" ({slot.last_temp}°C)" if slot.last_temp else ""
                        print(f"[GPU Pool] {agent_id} acquired {slot.name}{temp} for {model or 'unknown'}")
                        return slot
                remaining = deadline - time.time()
                if remaining <= 0:
                    print(f"[GPU Pool] {agent_id} timed out waiting for a slot")
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
                {
                    "id": s.id,
                    "name": s.name,
                    "url": s.url,
                    "backend": s.backend,
                    "vram_mb": s.vram_mb,
                    "in_use": s.in_use,
                    "agent": s.current_agent,
                    "temp": s.read_temp(),
                    "temp_warn": s.temp_warn,
                    "temp_crit": s.temp_crit,
                }
                for s in self.slots
            ]


gpu_pool = GPUPool()
