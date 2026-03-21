import requests
import json
from typing import Generator, Optional
from core.gpu_pool import gpu_pool, GPUSlot

OLLAMA_AMD_URL = "http://127.0.0.1:11434"   # AMD RX 6700 XT
OLLAMA_NV_URL  = "http://127.0.0.1:11435"   # NVIDIA RTX 3070
TIMEOUT_SECONDS = 300  # 5 minutes for large models


def chat_stream(model: str, messages: list, system_prompt: str = None,
                base_url: str = None) -> Generator[str, None, None]:
    """Stream a chat response from Ollama, yielding chunks as they arrive."""
    url = base_url or OLLAMA_AMD_URL
    payload = {
        "model": model,
        "messages": messages,
        "stream": True
    }
    if system_prompt:
        payload["system"] = system_prompt

    try:
        with requests.post(
            f"{url}/api/chat",
            json=payload,
            stream=True,
            timeout=TIMEOUT_SECONDS
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if line:
                    data = json.loads(line)
                    token = data.get("message", {}).get("content", "")
                    if token:
                        yield token
                    if data.get("done"):
                        break
    except requests.exceptions.Timeout:
        yield "\n\n_(response timed out)_"
    except Exception as e:
        yield f"\n\n_(error: {str(e)})_"


def chat_stream_pooled(model: str, messages: list, system_prompt: str = None,
                       agent_id: str = "unknown") -> Generator[str, None, None]:
    """
    Acquire a free GPU slot from the pool, stream inference, release when done.
    Blocks if both GPUs are busy until one frees up (max 120s wait).
    """
    slot: Optional[GPUSlot] = gpu_pool.acquire(agent_id, timeout=120.0)
    if slot is None:
        yield "_(No GPU available right now. Try again in a moment.)_"
        return

    try:
        yield from chat_stream(model, messages, system_prompt, base_url=slot.url)
    finally:
        gpu_pool.release(slot)


def is_available(base_url: str = OLLAMA_AMD_URL) -> bool:
    try:
        r = requests.get(f"{base_url}/api/tags", timeout=5)
        return r.status_code == 200
    except:
        return False


def both_instances_available() -> dict:
    return {
        "amd_vulkan":  is_available(OLLAMA_AMD_URL),
        "nvidia_cuda": is_available(OLLAMA_NV_URL),
    }
