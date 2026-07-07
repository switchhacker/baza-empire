"""Headless Fluid client for talk-to-agent + spoken replies.

Speaks the REAL Fluid contract (vision worktree, 127.0.0.1:8889):
- Sessions are minted only by ``GET /fluid`` — the id is embedded in the
  page HTML as ``<body ... data-session-id="...">`` (fluid.html).
- ``POST /api/fluid/say {session_id, text, agent_id}`` requires an existing
  session (404 otherwise); the reply arrives over SSE, not in the response.
- ``GET /api/fluid/stream?session_id=`` is an INFINITE SSE stream:
  ``id:`` / ``event:`` / ``data: <JSON object>`` frames plus ``: keep-alive``
  comment lines every ~15s. Reply sentences are ``agent_token`` events with
  the sentence in ``payload["text"]``; the turn ends at ``agent_turn_end``.
  The client enforces its own wall-clock deadline (``deadline_s``).
- ``POST /api/fluid/say_aloud {text, agent_id}`` needs no session.
"""
from __future__ import annotations
import json
import logging
import re
import time
from dataclasses import dataclass
import requests

log = logging.getLogger("voice_flow.agent_client")

# fluid.html: <body class="state-idle" data-session-id="{{ session_id }}">
_SESSION_RE = re.compile(r'data-session-id="([^"]+)"')


@dataclass
class AgentReply:
    text: str
    agent_id: str


class AgentClient:
    def __init__(self, fluid_url: str, default_agent: str,
                 session_id: str | None = None, http=requests,
                 deadline_s: float = 60.0, _now=time.monotonic):
        self._base = fluid_url.rstrip("/")
        self._default = default_agent
        self._session = session_id
        self._http = http
        self._deadline_s = deadline_s
        self._now = _now

    def _ensure_session(self) -> str:
        """Return a cached Fluid session id, minting one via GET /fluid if needed."""
        if self._session:
            return self._session
        resp = self._http.get(f"{self._base}/fluid", timeout=15)
        m = _SESSION_RE.search(getattr(resp, "text", "") or "")
        if not m:
            raise RuntimeError("could not obtain Fluid session")
        self._session = m.group(1)
        log.debug("minted Fluid session %s", self._session)
        return self._session

    def ask(self, transcript: str, agent_id: str | None = None) -> AgentReply:
        agent = agent_id or self._default
        stream = None
        for attempt in (0, 1):
            sid = self._ensure_session()
            # Open the stream BEFORE posting say so no token frames are missed.
            stream = self._http.get(f"{self._base}/api/fluid/stream",
                                    params={"session_id": sid}, stream=True,
                                    timeout=(10, 30))
            resp = self._http.post(f"{self._base}/api/fluid/say",
                                   json={"session_id": sid, "text": transcript,
                                         "agent_id": agent},
                                   timeout=30)
            status = getattr(resp, "status_code", 200)
            if status == 404 and attempt == 0:
                # Stale/unknown session: drop it, re-mint once, retry.
                log.info("Fluid session %s not found; re-minting", sid)
                self._session = None
                self._close(stream)
                continue
            if status != 200:
                self._close(stream)
                raise RuntimeError(f"fluid say failed: HTTP {status}")
            break
        text = self._assemble(stream)
        return AgentReply(text=text, agent_id=agent)

    def _assemble(self, resp) -> str:
        """Read SSE frames until agent_turn_end or the wall-clock deadline."""
        parts: list[str] = []
        event = None
        start = self._now()
        try:
            for raw in resp.iter_lines():
                if self._now() - start > self._deadline_s:
                    log.warning("Fluid stream deadline (%.0fs) hit; returning "
                                "partial reply", self._deadline_s)
                    break
                if raw is None:
                    continue
                line = raw.decode("utf-8", "replace") \
                    if isinstance(raw, (bytes, bytearray)) else raw
                line = line.strip()
                if not line or line.startswith(":"):  # blank / keep-alive
                    continue
                if line.startswith("event:"):
                    event = line[len("event:"):].strip()
                    if event == "agent_turn_end":
                        break
                    continue
                if line.startswith("data:") and event == "agent_token":
                    try:
                        payload = json.loads(line[len("data:"):].strip())
                    except ValueError:
                        continue
                    if isinstance(payload, dict):
                        sentence = payload.get("text", "")
                        if sentence:
                            parts.append(sentence)
        finally:
            self._close(resp)
        return " ".join(parts).strip()

    @staticmethod
    def _close(resp) -> None:
        try:
            if resp is not None:
                resp.close()
        except Exception:  # noqa: BLE001
            pass

    def speak(self, text: str, agent_id: str) -> None:
        if not text:
            return
        self._http.post(f"{self._base}/api/fluid/say_aloud",
                        json={"text": text, "agent_id": agent_id}, timeout=60)
