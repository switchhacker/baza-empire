from unittest.mock import MagicMock
from voice_flow.config import load_config
from voice_flow.daemon import Daemon


def _daemon(**over):
    cfg = load_config()
    tr = MagicMock(); tr.transcribe.return_value = "hello there"
    inj = MagicMock(); inj.inject.return_value = len("hello there")
    d = Daemon(config=cfg, transcriber=tr, injector=inj,
               recorder_factory=MagicMock(), commands_enabled=False, **over)
    return d, tr, inj


def test_raw_mode_transcribes_and_injects():
    d, tr, inj = _daemon()
    out = d.handle_utterance("raw", "/tmp/x.wav")
    tr.transcribe.assert_called_once_with("/tmp/x.wav")
    inj.inject.assert_called_once_with("hello there")
    assert out == "hello there"


def test_empty_transcript_injects_nothing():
    d, tr, inj = _daemon()
    tr.transcribe.return_value = "   "
    out = d.handle_utterance("raw", "/tmp/x.wav")
    inj.inject.assert_not_called()
    assert out == ""
