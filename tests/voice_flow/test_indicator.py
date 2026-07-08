from unittest.mock import MagicMock
from voice_flow.indicator import Indicator, _GLYPH


def test_glyphs_are_latin1_encodable():
    # pystray's X backend encodes the tray title as latin-1; emoji glyphs
    # raise UnicodeEncodeError and force headless mode. Keep labels ASCII-safe.
    for state, glyph in _GLYPH.items():
        f"Baza Flow {glyph}".encode("latin-1")  # must not raise


def test_set_state_records_current_state():
    ind = Indicator(chimes=False, runner=MagicMock())
    ind.set_state("listening")
    assert ind.state == "listening"


def test_chime_plays_when_enabled():
    run = MagicMock()
    ind = Indicator(chimes=True, runner=run)
    ind.chime("start")
    assert run.called


def test_chime_silent_when_disabled():
    run = MagicMock()
    ind = Indicator(chimes=False, runner=run)
    ind.chime("start")
    run.assert_not_called()


def test_never_raises_on_runner_error():
    run = MagicMock(side_effect=OSError("no audio"))
    ind = Indicator(chimes=True, runner=run)
    ind.chime("start")  # must not raise
    ind.set_state("idle")
