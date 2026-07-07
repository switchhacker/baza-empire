"""Baza Flow daemon: mode state machine + utterance handling."""
from __future__ import annotations
import logging

log = logging.getLogger("voice_flow.daemon")


class Daemon:
    def __init__(self, config, transcriber, injector, recorder_factory,
                 flow_fn=None, agent_client=None, commands_enabled=True, indicator=None):
        self.cfg = config
        self.transcriber = transcriber
        self.injector = injector
        self.recorder_factory = recorder_factory
        self.flow_fn = flow_fn
        self.agent_client = agent_client
        self.commands_enabled = commands_enabled
        self.indicator = indicator
        self._recorder = None
        self._last_injected = 0
        self.active_dictation_mode = "raw"

    # --- hotkey callbacks (wired in main()) ---
    def on_press(self, mode: str) -> None:
        if mode == "cancel":
            self._abort(); return
        self._set_state("listening")
        self._recorder = self.recorder_factory()
        self._recorder.start()

    def on_release(self, mode: str) -> None:
        if mode == "cancel" or self._recorder is None:
            return
        wav = self._recorder.stop()
        self._recorder = None
        self._set_state("thinking")
        try:
            self.handle_utterance(mode, wav)
        finally:
            self._set_state("idle")

    def _abort(self) -> None:
        if self._recorder is not None:
            self._recorder.abort()
            self._recorder = None
        self._set_state("idle")

    def _set_state(self, s: str) -> None:
        if self.indicator is not None:
            self.indicator.set_state(s)

    # --- deterministic core (tested) ---
    def handle_utterance(self, mode: str, wav_path: str) -> str:
        text = (self.transcriber.transcribe(wav_path) or "").strip()
        if not text:
            return ""
        # command interception happens in Task 6 (guarded by commands_enabled)
        if mode == "agent" and self.agent_client is not None:
            return self._do_agent(text)
        if mode == "flow" and self.flow_fn is not None:
            text = self.flow_fn(text) or text
        self._last_injected = self.injector.inject(text)
        return text

    def _do_agent(self, text: str) -> str:
        reply = self.agent_client.ask(text)
        if self.cfg.agent.get("speak_reply", True):
            self.agent_client.speak(reply.text, reply.agent_id)
        if self.cfg.agent.get("type_reply", False):
            self.injector.inject(reply.text)
        return reply.text
