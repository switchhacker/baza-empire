import requests
import json
import os
from typing import Generator, Optional
from core.gpu_pool import gpu_pool, GPUSlot

OLLAMA_AMD_URL  = "http://127.0.0.1:11434"   # AMD RX 6700 XT (Vulkan)
OLLAMA_NV_URL   = "http://127.0.0.1:11435"   # NVIDIA RTX 3070 (CUDA)
LITELLM_URL     = os.environ.get("LITELLM_URL", "http://127.0.0.1:4000")  # Cloud proxy
LITELLM_KEY     = os.environ.get("LITELLM_MASTER_KEY", "baza-litellm-internal")
TIMEOUT_SECONDS = 300  # 5 minutes for large models

# Models that should be routed to the LiteLLM cloud proxy
CLOUD_MODEL_PREFIXES = (
    "gpt-", "claude-", "gemini-", "grok-", "mistral-large", "codestral",
    "groq-", "o1", "o3-", "local/"
)


def is_cloud_model(model: str) -> bool:
    return any(model.startswith(p) for p in CLOUD_MODEL_PREFIXES)


def chat_stream_cloud(model: str, messages: list, system_prompt: str = None) -> Generator[str, None, None]:
    """Stream from LiteLLM proxy using OpenAI-compatible API (cloud + local models)."""
    full_messages = list(messages)
    if system_prompt:
        full_messages = [{"role": "system", "content": system_prompt}] + full_messages

    payload = {
        "model": model,
        "messages": full_messages,
        "stream": True,
        "max_tokens": 2000,
        "temperature": 0.7,
    }
    headers = {
        "Authorization": f"Bearer {LITELLM_KEY}",
        "Content-Type": "application/json",
    }
    try:
        with requests.post(
            f"{LITELLM_URL}/v1/chat/completions",
            json=payload,
            headers=headers,
            stream=True,
            timeout=TIMEOUT_SECONDS,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                text = line.decode("utf-8") if isinstance(line, bytes) else line
                if text.startswith("data: "):
                    text = text[6:]
                if text == "[DONE]":
                    break
                try:
                    data = json.loads(text)
                    token = data["choices"][0]["delta"].get("content", "")
                    if token:
                        yield token
                except Exception:
                    pass
    except requests.exceptions.Timeout:
        yield "\n\n_(cloud response timed out)_"
    except Exception as e:
        yield f"\n\n_(cloud error: {str(e)})_"


def chat_stream(model: str, messages: list, system_prompt: str = None,
                base_url: str = None) -> Generator[str, None, None]:
    """Stream a chat response from Ollama, yielding chunks as they arrive."""
    url = base_url or OLLAMA_AMD_URL
    # Inject system prompt as first message — Ollama's top-level "system" field
    # is unreliable across versions. messages[role=system] works universally.
    full_messages = messages
    if system_prompt:
        full_messages = [{"role": "system", "content": system_prompt}] + list(messages)

    payload = {
        "model": model,
        "messages": full_messages,
        "stream": True,
        "options": {
            "num_predict": 600,    # cap response length — prevents essay mode
            "temperature": 0.7
        }
    }

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
    Route to cloud (LiteLLM) or acquire a GPU slot and stream from Ollama.
    Cloud models bypass the GPU pool entirely.
    """
    if is_cloud_model(model):
        yield from chat_stream_cloud(model, messages, system_prompt)
        return

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
    litellm_up = False
    try:
        r = requests.get(f"{LITELLM_URL}/health", timeout=3)
        litellm_up = r.status_code < 500
    except:
        pass
    return {
        "amd_vulkan":  is_available(OLLAMA_AMD_URL),
        "nvidia_cuda": is_available(OLLAMA_NV_URL),
        "litellm_cloud": litellm_up,
    }


def list_available_models() -> dict:
    """Return all models: local Ollama + cloud via LiteLLM."""
    result = {"local_amd": [], "local_cuda": [], "cloud": []}
    for url, key in [(OLLAMA_AMD_URL, "local_amd"), (OLLAMA_NV_URL, "local_cuda")]:
        try:
            r = requests.get(f"{url}/api/tags", timeout=5)
            if r.ok:
                result[key] = [m["name"] for m in r.json().get("models", [])]
        except:
            pass
    try:
        r = requests.get(f"{LITELLM_URL}/v1/models",
                         headers={"Authorization": f"Bearer {LITELLM_KEY}"}, timeout=5)
        if r.ok:
            result["cloud"] = [m["id"] for m in r.json().get("data", [])]
    except:
        pass
    return result
