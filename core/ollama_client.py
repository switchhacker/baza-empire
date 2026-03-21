import requests
import json
from typing import Generator, Optional
from core.gpu_pool import gpu_pool, GPUSlot

# Fallback single-instance URL (used when not going through pool)
OLLAMA_BASE_URL = "http://localhost:11434"


def chat(model: str, messages: list, system_prompt: str = None,
         agent_id: str = "unknown", base_url: str = None) -> str:
    """Send a chat request to Ollama and return the full response (non-streaming)."""
    url = base_url or OLLAMA_BASE_URL
    payload = {
        "model": model,
        "messages": messages,
        "stream": False
    }
    if system_prompt:
        payload["system"] = system_prompt

    try:
        response = requests.post(
            f"{url}/api/chat",
            json=payload,
            timeout=120
        )
        response.raise_for_status()
        return response.json()["message"]["content"]
    except requests.exceptions.Timeout:
        return "I'm taking too long to think. Try again."
    except Exception as e:
        return f"Model error: {str(e)}"


def chat_stream(model: str, messages: list, system_prompt: str = None,
                base_url: str = None) -> Generator[str, None, None]:
    """Stream a chat response from Ollama, yielding chunks as they arrive."""
    url = base_url or OLLAMA_BASE_URL
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
            timeout=180
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


def chat_pooled(model: str, messages: list, system_prompt: str = None,
                agent_id: str = "unknown") -> str:
    """
    Acquire a free GPU from the pool, run inference, release the GPU.
    Blocks if both GPUs are busy until one frees up.
    """
    slot: Optional[GPUSlot] = gpu_pool.acquire(agent_id)
    if slot is None:
        return "No GPU available. Try again."

    try:
        return chat(model, messages, system_prompt,
                    agent_id=agent_id, base_url=slot.url)
    finally:
        gpu_pool.release(slot)


def chat_stream_pooled(model: str, messages: list, system_prompt: str = None,
                       agent_id: str = "unknown") -> Generator[str, None, None]:
    """
    Acquire a free GPU from the pool, stream inference, release when done.
    Blocks if both GPUs are busy. Use this for group chat sequential streaming.
    """
    slot: Optional[GPUSlot] = gpu_pool.acquire(agent_id)
    if slot is None:
        yield "_(No GPU available. Try again.)_"
        return

    try:
        yield from chat_stream(model, messages, system_prompt, base_url=slot.url)
    finally:
        gpu_pool.release(slot)


def is_available(base_url: str = OLLAMA_BASE_URL) -> bool:
    """Check if an Ollama instance is running."""
    try:
        r = requests.get(f"{base_url}/api/tags", timeout=5)
        return r.status_code == 200
    except:
        return False


def both_instances_available() -> dict:
    """Check health of both GPU instances."""
    return {
        "amd_vulkan": is_available("http://localhost:11434"),
        "nvidia_cuda": is_available("http://localhost:11435"),
    }
