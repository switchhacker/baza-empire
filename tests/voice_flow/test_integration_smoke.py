import numpy as np
from unittest.mock import MagicMock, patch
from voice_flow.config import load_config
from voice_flow.recorder import frames_to_wav
from voice_flow.daemon import Daemon, build_daemon


def test_build_daemon_returns_daemon():
    cfg = load_config()
    with patch("voice_flow.daemon.Transcriber"), \
         patch("voice_flow.daemon.AgentClient"):
        d = build_daemon(cfg)
    assert isinstance(d, Daemon)


def test_end_to_end_raw_injection_with_fixture_wav(tmp_path):
    wav = frames_to_wav((np.random.randn(16000) * 0.01).astype("float32"),
                        16000, str(tmp_path / "u.wav"))
    cfg = load_config()
    tr = MagicMock(); tr.transcribe.return_value = "book the crew for tuesday"
    inj = MagicMock(); inj.inject.return_value = 25
    d = Daemon(config=cfg, transcriber=tr, injector=inj,
               recorder_factory=MagicMock(), commands_enabled=True)
    out = d.handle_utterance("raw", wav)
    assert out == "book the crew for tuesday"
    inj.inject.assert_called_once_with("book the crew for tuesday")


def test_end_to_end_agent_mode_speaks(tmp_path):
    cfg = load_config()
    tr = MagicMock(); tr.transcribe.return_value = "specter what is our cash position"
    inj = MagicMock()
    ac = MagicMock()
    from voice_flow.agent_client import AgentReply
    ac.ask.return_value = AgentReply(text="Cash is healthy.", agent_id="specter_voss")
    d = Daemon(config=cfg, transcriber=tr, injector=inj, recorder_factory=MagicMock(),
               agent_client=ac, commands_enabled=True)
    out = d.handle_utterance("agent", str(tmp_path / "x.wav"))
    ac.ask.assert_called_once()
    ac.speak.assert_called_once_with("Cash is healthy.", "specter_voss")
    assert out == "Cash is healthy."
