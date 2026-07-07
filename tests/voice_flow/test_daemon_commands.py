from unittest.mock import MagicMock
from voice_flow.commands import AGENT_NAMES, AGENT_ID_BY_NAME, Command, match_command
from voice_flow.config import load_config
from voice_flow.daemon import Daemon


def _daemon():
    cfg = load_config()
    tr = MagicMock(); inj = MagicMock(); inj.inject.return_value = 5
    return Daemon(config=cfg, transcriber=tr, injector=inj,
                  recorder_factory=MagicMock(), commands_enabled=True), tr, inj


def _agent_daemon():
    cfg = load_config()
    tr = MagicMock(); inj = MagicMock(); inj.inject.return_value = 5
    ac = MagicMock()
    ac.ask.return_value = MagicMock(text="hi Serge", agent_id="specter_voss")
    d = Daemon(config=cfg, transcriber=tr, injector=inj,
               recorder_factory=MagicMock(), agent_client=ac,
               commands_enabled=True)
    return d, tr, inj, ac


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


# --- B2: short spoken names map to full Fluid agent ids ---

def test_every_agent_name_has_full_id_mapping():
    for name in AGENT_NAMES:
        assert name in AGENT_ID_BY_NAME, f"{name} missing from AGENT_ID_BY_NAME"
        assert "_" in AGENT_ID_BY_NAME[name]  # full ids are first_last
    assert AGENT_ID_BY_NAME["specter"] == "specter_voss"


def test_send_to_nova_still_routes():
    assert match_command("send to nova", AGENT_NAMES) == Command("route", "nova")


# --- B3: "send to <agent>" targets the NEXT utterance ---

def test_route_command_targets_next_utterance():
    d, tr, inj, ac = _agent_daemon()
    tr.transcribe.return_value = "send to specter"
    assert d.handle_utterance("raw", "/tmp/x.wav") == ""
    ac.ask.assert_not_called()
    assert d._pending_agent == "specter"
    tr.transcribe.return_value = "what is on the schedule today"
    out = d.handle_utterance("raw", "/tmp/x.wav")
    ac.ask.assert_called_once_with("what is on the schedule today",
                                   agent_id="specter_voss")
    assert d._pending_agent is None
    assert out == "hi Serge"


def test_normal_utterance_without_pending_still_dictates():
    d, tr, inj, ac = _agent_daemon()
    tr.transcribe.return_value = "hello world"
    out = d.handle_utterance("raw", "/tmp/x.wav")
    inj.inject.assert_called_once_with("hello world")
    ac.ask.assert_not_called()
    assert out == "hello world"
