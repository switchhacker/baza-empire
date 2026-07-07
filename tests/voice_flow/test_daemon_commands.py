from unittest.mock import MagicMock
from voice_flow.config import load_config
from voice_flow.daemon import Daemon


def _daemon():
    cfg = load_config()
    tr = MagicMock(); inj = MagicMock(); inj.inject.return_value = 5
    return Daemon(config=cfg, transcriber=tr, injector=inj,
                  recorder_factory=MagicMock(), commands_enabled=True), tr, inj


def test_new_line_command_presses_return_not_types():
    d, tr, inj = _daemon()
    tr.transcribe.return_value = "new line"
    out = d.handle_utterance("raw", "/tmp/x.wav")
    inj.press.assert_called_once_with("Return")
    inj.inject.assert_not_called()
    assert out == ""


def test_switch_to_flow_changes_active_mode():
    d, tr, inj = _daemon()
    tr.transcribe.return_value = "switch to flow"
    d.handle_utterance("raw", "/tmp/x.wav")
    assert d.active_dictation_mode == "flow"
