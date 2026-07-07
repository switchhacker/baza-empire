"""Wave B integration tests: thread-safe pipeline (B4), chimes/tray (B5)."""
import sys
from unittest.mock import MagicMock, patch

from voice_flow.config import load_config
from voice_flow.daemon import Daemon
from voice_flow.indicator import Indicator


def _daemon(agent_client=None, indicator=None, commands_enabled=False):
    cfg = load_config()
    tr = MagicMock(); tr.transcribe.return_value = "hello"
    inj = MagicMock(); inj.inject.return_value = 5
    rec = MagicMock()
    factory = MagicMock(return_value=rec)
    d = Daemon(config=cfg, transcriber=tr, injector=inj,
               recorder_factory=factory, agent_client=agent_client,
               commands_enabled=commands_enabled, indicator=indicator)
    return d, tr, inj, rec, factory


# --- B4: pipeline errors never propagate, daemon stays usable ---

def test_process_swallows_exception_and_chimes_error():
    ind = MagicMock()
    ac = MagicMock(); ac.ask.side_effect = RuntimeError("fluid down")
    d, tr, inj, rec, factory = _daemon(agent_client=ac, indicator=ind)
    d.active_dictation_mode = "agent"
    d._process("agent", "/tmp/x.wav")  # must not raise
    ind.chime.assert_any_call("error")
    ind.set_state.assert_called_with("idle")
    assert d._busy is False


def test_on_release_submits_off_thread_and_completes():
    ind = MagicMock()
    d, tr, inj, rec, factory = _daemon(indicator=ind)
    d.on_press("raw")
    d.on_release("raw")
    d._last_future.result(timeout=5)  # pipeline ran on the executor thread
    inj.inject.assert_called_once_with("hello")
    assert d._busy is False


def test_press_ignored_while_busy():
    d, tr, inj, rec, factory = _daemon()
    d._busy = True
    d.on_press("raw")
    factory.assert_not_called()


def test_daemon_still_usable_after_error():
    ind = MagicMock()
    ac = MagicMock(); ac.ask.side_effect = RuntimeError("boom")
    d, tr, inj, rec, factory = _daemon(agent_client=ac, indicator=ind)
    d.active_dictation_mode = "agent"
    d._process("agent", "/tmp/x.wav")
    # next capture still works
    d.on_press("raw")
    factory.assert_called_once()


# --- B5: chimes on press/release; tray start is never fatal ---

def test_press_and_release_fire_chimes():
    ind = MagicMock()
    d, tr, inj, rec, factory = _daemon(indicator=ind)
    d.on_press("raw")
    ind.chime.assert_called_with("start")
    d.on_release("raw")
    assert any(c.args == ("stop",) for c in ind.chime.call_args_list)
    d._last_future.result(timeout=5)


def test_indicator_start_never_raises_when_pystray_missing():
    ind = Indicator(chimes=False, runner=MagicMock())
    with patch.dict(sys.modules, {"pystray": None}):
        ind.start()  # ImportError inside must be swallowed
    assert ind._tray is None


def test_indicator_start_creates_detached_tray_when_available():
    fake_pystray = MagicMock()
    ind = Indicator(chimes=False, runner=MagicMock())
    with patch.dict(sys.modules, {"pystray": fake_pystray}):
        ind.start()
    fake_pystray.Icon.assert_called_once()
    fake_pystray.Icon.return_value.run_detached.assert_called_once()
    assert ind._tray is fake_pystray.Icon.return_value
