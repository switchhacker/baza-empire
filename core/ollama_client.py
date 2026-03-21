import requests
import json
from typing import Generator

OLLAMA_BASE_URL = "http://localhost:11434"
TIMEOUT_SECONDS = 300  # 5 minutes — large models need time


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
        yield "\n\n_(response timed out — model may be overloaded)_"
    except Exception as e:
        yield f"\n\n_(error: {str(e)})_"


# Alias — single instance for now, GPU pool ready when 2nd Ollama is configured
def chat_stream_pooled(model: str, messages: list, system_prompt: str = None,
                       agent_id: str = "unknown") -> Generator[str, None, None]:
    """
    Single-instance streaming for now.
    GPU pool will activate when port 11435 (NVIDIA) instance is running.
    """
    yield from chat_stream(model, messages, system_prompt)


def is_available(base_url: str = OLLAMA_BASE_URL) -> bool:
    try:
        r = requests.get(f"{base_url}/api/tags", timeout=5)
        return r.status_code == 200
    except:
        return False


def both_instances_available() -> dict:
    return {
        "amd_vulkan": is_available("http://localhost:11434"),
        "nvidia_cuda": is_available("http://localhost:11435"),
    }
