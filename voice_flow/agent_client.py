"""Headless Fluid client for talk-to-agent + spoken replies."""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass
import requests

log = logging.getLogger("voice_flow.agent_client")


@dataclass
class AgentReply:
    text: str
    agent_id: str


class AgentClient:
    def __init__(self, fluid_url: str, default_agent: str,
                 session_id: str = "baza-flow", http=requests):
        self._base = fluid_url.rstrip("/")
        self._default = default_agent
        self._sid = session_id
        self._http = http

    def ask(self, transcript: str, agent_id: str | None = None) -> AgentReply:
        agent = agent_id or self._default
        self._http.post(f"{self._base}/api/fluid/say",
                        json={"session_id": self._sid, "text": transcript, "agent_id": agent},
                        timeout=30)
        resp = self._http.get(f"{self._base}/api/fluid/stream",
                              params={"session_id": self._sid}, stream=True, timeout=120)
        text = self._assemble(resp)
        return AgentReply(text=text, agent_id=agent)

    def _assemble(self, resp) -> str:
        event = None
        parts: list[str] = []
        for raw in resp.iter_lines():
            line = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
            if line is None:
                continue
            line = line.strip()
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                payload = line[5:].strip()
                if event in ("done", "end"):
                    break
                if event == "agent_token":
                    try:
                        payload = json.loads(payload)
                    except Exception:  # noqa: BLE001
                        pass
                    parts.append(payload if isinstance(payload, str) else "")
        return "".join(parts).strip()

    def speak(self, text: str, agent_id: str) -> None:
        if not text:
            return
        self._http.post(f"{self._base}/api/fluid/say_aloud",
                        json={"text": text, "agent_id": agent_id}, timeout=60)
