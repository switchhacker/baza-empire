"""Local Ollama cleanup/format pass for flow dictation."""
from __future__ import annotations
import logging
import requests

log = logging.getLogger("voice_flow.flow")


def make_flow(cfg_flow: dict, poster=requests.post):
    url = cfg_flow.get("ollama_url", "http://127.0.0.1:11434/api/generate")
    model = cfg_flow.get("model", "gemma4:12b-it-qat")
    system = cfg_flow.get("system_prompt", "")
    temperature = cfg_flow.get("temperature", 0)

    def flow_fn(text: str) -> str:
        try:
            resp = poster(url, json={
                "model": model, "prompt": text, "system": system,
                "stream": False, "options": {"temperature": temperature},
            }, timeout=60)
            resp.raise_for_status()
            return (resp.json().get("response") or text).strip()
        except Exception as e:  # noqa: BLE001
            log.warning("flow cleanup failed (%s); returning raw text", e)
            return text

    return flow_fn
