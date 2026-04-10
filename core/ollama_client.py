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
    """Check if model should route to LiteLLM cloud proxy."""
    return any(model.startswith(p) for p in CLOUD_MODEL_PREFIXES)


def is_ollama_cloud_model(model: str) -> bool:
    """Check if model is an Ollama cloud model (runs via local Ollama proxy to Ollama cloud)."""
    return model.endswith(":cloud")


def chat_stream_cloud(model: str, messages: list, system_prompt: str = None,
                      on_complete=None) -> Generator[str, None, None]:
    """Stream from LiteLLM proxy using OpenAI-compatible API (cloud + local models)."""
    full_messages = list(messages)
    if system_prompt:
        full_messages = [{"role": "system", "content": system_prompt}] + full_messages

    payload = {
        "model": model,
        "messages": full_messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_tokens": 2000,
        "temperature": 0.7,
    }
    headers = {
        "Authorization": f"Bearer {LITELLM_KEY}",
        "Content-Type": "application/json",
    }
    usage_data = {}
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
                    # Capture usage from final chunk
                    if "usage" in data:
                        u = data["usage"]
                        usage_data = {
                            "prompt_tokens": u.get("prompt_tokens", 0),
                            "completion_tokens": u.get("completion_tokens", 0),
                            "total_duration_ns": 0,
                            "model": model, "provider": "litellm"
                        }
                except Exception:
                    pass
    except requests.exceptions.Timeout:
        yield "\n\n_(cloud response timed out)_"
    except Exception as e:
        yield f"\n\n_(cloud error: {str(e)})_"
    finally:
        if on_complete and usage_data:
            on_complete(usage_data)


def chat_stream(model: str, messages: list, system_prompt: str = None,
                base_url: str = None, on_complete=None) -> Generator[str, None, None]:
    """Stream a chat response from Ollama, yielding chunks as they arrive."""
    url = base_url or OLLAMA_AMD_URL
    full_messages = messages
    if system_prompt:
        full_messages = [{"role": "system", "content": system_prompt}] + list(messages)

    # Dynamically size context to fit the full conversation + headroom
    total_chars = sum(len(m.get("content", "")) for m in full_messages)
    needed_ctx = max(8192, (total_chars // 3) + 1200)  # ~3 chars/token + output budget
    if needed_ctx > 32768:
        needed_ctx = 32768  # cap at 32k

    payload = {
        "model": model,
        "messages": full_messages,
        "stream": True,
        "options": {
            "num_predict": 600,
            "num_ctx": needed_ctx,
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
                        if on_complete:
                            on_complete({
                                "prompt_tokens": data.get("prompt_eval_count", 0),
                                "completion_tokens": data.get("eval_count", 0),
                                "total_duration_ns": data.get("total_duration", 0),
                                "model": model, "provider": "ollama"
                            })
                        break
    except requests.exceptions.Timeout:
        yield "\n\n_(response timed out)_"
    except Exception as e:
        yield f"\n\n_(error: {str(e)})_"


# ── Graft 4: rate-limit fallback ──────────────────────────────────────────────
# Cloud models (Ollama Cloud, OpenAI, Anthropic, etc.) periodically return
# "rate limit reached" / 429 / "quota exceeded" — without a fallback, the agent
# silently dies. We buffer the first ~300 chars of the primary stream, and if
# that prefix matches a rate-limit marker we discard it and re-stream from the
# agent's local fallback model. Normal responses blow past the buffer in a
# fraction of a second so streaming UX is preserved.
RATE_LIMIT_MARKERS = (
    "rate limit reached",
    "rate-limit",
    "rate_limit",
    "rate limit exceeded",
    "429",
    "quota exceeded",
    "too many requests",
    "rate_limit_exceeded",
    "api rate limit",
    "insufficient_quota",
)

DEFAULT_FALLBACK_MODEL = os.environ.get("DEFAULT_FALLBACK_MODEL", "qwen2.5:14b")


def _looks_rate_limited(text: str) -> bool:
    if not text:
        return False
    low = text.lower()
    return any(m in low for m in RATE_LIMIT_MARKERS)


def _route_stream(model: str, messages: list, system_prompt, agent_id: str, on_complete):
    """Lower-level stream routing (cloud / ollama-cloud / pooled local) without fallback."""
    if is_cloud_model(model):
        yield from chat_stream_cloud(model, messages, system_prompt, on_complete=on_complete)
        return
    if is_ollama_cloud_model(model):
        ollama_url = os.environ.get("OLLAMA_URL", OLLAMA_AMD_URL)
        yield from chat_stream(model, messages, system_prompt, base_url=ollama_url, on_complete=on_complete)
        return
    slot: Optional[GPUSlot] = gpu_pool.acquire(agent_id, timeout=120.0, model=model)
    if slot is None:
        yield "_(No GPU available right now. Try again in a moment.)_"
        return
    try:
        yield from chat_stream(model, messages, system_prompt, base_url=slot.url, on_complete=on_complete)
    finally:
        gpu_pool.release(slot)


def chat_stream_pooled(model: str, messages: list, system_prompt: str = None,
                       agent_id: str = "unknown", on_complete=None,
                       fallback_model: str = None) -> Generator[str, None, None]:
    """
    Route to cloud (LiteLLM) or acquire a GPU slot and stream from Ollama.
    Cloud models bypass the GPU pool entirely.

    If the primary model returns a rate-limit error in its first ~300 chars,
    transparently retry with `fallback_model` (defaults to DEFAULT_FALLBACK_MODEL).
    A small banner is prepended so the user knows the fallback kicked in.
    """
    fb = fallback_model or DEFAULT_FALLBACK_MODEL

    # Local-only models can't be rate-limited — skip the buffering overhead
    if not (is_cloud_model(model) or is_ollama_cloud_model(model)):
        yield from _route_stream(model, messages, system_prompt, agent_id, on_complete)
        return

    # Same model for primary + fallback → no point in retrying
    if fb == model:
        yield from _route_stream(model, messages, system_prompt, agent_id, on_complete)
        return

    BUFFER_LIMIT = 300
    buffer = ""
    committed = False

    try:
        for chunk in _route_stream(model, messages, system_prompt, agent_id, on_complete):
            if committed:
                yield chunk
                continue
            buffer += chunk
            if len(buffer) >= BUFFER_LIMIT:
                committed = True
                yield buffer
                buffer = ""
    except Exception as e:
        buffer += f" {e}"

    if committed:
        return

    # Stream ended (or errored) within buffer window — decide
    if _looks_rate_limited(buffer):
        banner = f"🛡️ _({model} rate-limited — running on local {fb})_\n\n"
        yield banner
        yield from _route_stream(fb, messages, system_prompt, agent_id, on_complete)
        return

    if buffer:
        yield buffer


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
