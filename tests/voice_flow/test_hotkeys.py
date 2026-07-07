from voice_flow.hotkeys import parse_chord


def test_parse_chord_normalizes():
    assert parse_chord("Ctrl+Space") == frozenset({"ctrl", "space"})
    assert parse_chord("ctrl+shift+space") == frozenset({"ctrl", "shift", "space"})
    assert parse_chord("esc") == frozenset({"esc"})
