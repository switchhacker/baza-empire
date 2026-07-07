"""Baza Flow daemon: mode state machine + utterance handling."""
from __future__ import annotations
import logging

from voice_flow.commands import match_command, AGENT_NAMES

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
        if self.commands_enabled:
            cmd = match_command(text, AGENT_NAMES)
            if cmd is not None:
                self._run_command(cmd)
                return ""
        effective = self.active_dictation_mode if mode in ("raw", "flow") else mode
        if effective == "agent" and self.agent_client is not None:
            return self._do_agent(text)
        if effective == "flow" and self.flow_fn is not None:
            text = self.flow_fn(text) or text
        self._last_injected = self.injector.inject(text)
        return text

    def _run_command(self, cmd) -> None:
        if cmd.action == "newline":
            self.injector.press("Return")
        elif cmd.action == "paragraph":
            self.injector.press("Return"); self.injector.press("Return")
        elif cmd.action == "scratch":
            self.injector.delete_last(self._last_injected); self._last_injected = 0
        elif cmd.action == "select_all":
            self.injector.press("ctrl+a")
        elif cmd.action == "undo":
            self.injector.press("ctrl+z")
        elif cmd.action == "set_mode":
            self.active_dictation_mode = cmd.arg
        elif cmd.action == "route":
            self._pending_agent = cmd.arg
        elif cmd.action == "stop":
            self._abort()

    def _do_agent(self, text: str) -> str:
        reply = self.agent_client.ask(text)
        if self.cfg.agent.get("speak_reply", True):
            self.agent_client.speak(reply.text, reply.agent_id)
        if self.cfg.agent.get("type_reply", False):
            self.injector.inject(reply.text)
        return reply.text


from voice_flow.stt import Transcriber
from voice_flow.inject import Injector
from voice_flow.flow import make_flow
from voice_flow.agent_client import AgentClient
from voice_flow.indicator import Indicator
from voice_flow.recorder import Recorder
from voice_flow.hotkeys import HotkeyListener
from voice_flow.config import load_config


def build_daemon(config):
    stt = config.stt
    transcriber = Transcriber(
        model=stt.get("model", "base"),
        compute_type=stt.get("compute_type", "int8"),
        device=stt.get("device", "cpu"),
        fallback_url=stt.get("fluid_stt_fallback"),
    )
    injector = Injector(method=config.injection.get("method", "paste"))
    flow_fn = make_flow(config.flow)
    agent_client = AgentClient(
        fluid_url=config.agent.get("fluid_url", "http://127.0.0.1:8889"),
        default_agent=config.agent.get("default_agent", "specter_voss"),
    )
    indicator = Indicator(chimes=config.audio.get("chimes", True))
    sr = config.audio.get("samplerate", 16000)
    dev = config.audio.get("input_device")
    return Daemon(
        config=config, transcriber=transcriber, injector=injector,
        recorder_factory=lambda: Recorder(device=dev, samplerate=sr),
        flow_fn=flow_fn, agent_client=agent_client,
        commands_enabled=config.commands.get("enabled", True), indicator=indicator,
    )


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    config = load_config()
    daemon = build_daemon(config)
    daemon.indicator.start()
    bindings = {
        config.hotkeys.get("raw", "ctrl+space"): "raw",
        config.hotkeys.get("flow", "ctrl+shift+space"): "flow",
        config.hotkeys.get("agent", "ctrl+alt+space"): "agent",
        config.hotkeys.get("cancel", "esc"): "cancel",
    }
    listener = HotkeyListener(bindings, on_press=daemon.on_press, on_release=daemon.on_release)
    listener.start()
    log.info("Baza Flow ready. Hotkeys: %s", bindings)
    import signal
    signal.pause()


if __name__ == "__main__":
    main()
