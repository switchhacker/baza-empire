from unittest.mock import MagicMock
from voice_flow.inject import Injector


def _runner_recording():
    calls = []
    def run(cmd, **kw):
        calls.append((cmd, kw))
        m = MagicMock()
        m.stdout = b"OLD_CLIP"
        m.returncode = 0
        return m
    return run, calls


def test_paste_sets_clipboard_and_sends_ctrl_v():
    run, calls = _runner_recording()
    inj = Injector(method="paste", runner=run, restore_delay=0)
    n = inj.inject("hello world")
    assert n == len("hello world")
    argvs = [c[0] for c in calls]
    # clipboard read, clipboard write (new), ctrl+v, clipboard restore
    assert any("xclip" in a and "-o" in a for a in argvs)
    assert any(a[:3] == ["xdotool", "key", "ctrl+v"] for a in argvs)


def _runner_with_window_class(wm_class_line):
    calls = []
    def run(cmd, **kw):
        calls.append((cmd, kw))
        m = MagicMock()
        m.returncode = 0
        if cmd[:2] == ["xdotool", "getactivewindow"]:
            m.stdout = b"0x3c000a5\n"
        elif cmd[0] == "xprop":
            m.stdout = wm_class_line.encode()
        else:
            m.stdout = b"OLD_CLIP"
        return m
    return run, calls


def test_paste_uses_ctrl_shift_v_in_terminal():
    run, calls = _runner_with_window_class(
        'WM_CLASS(STRING) = "gnome-terminal-server", "Gnome-terminal"\n')
    inj = Injector(method="paste", runner=run, restore_delay=0)
    inj.inject("hello")
    argvs = [c[0] for c in calls]
    assert ["xdotool", "key", "ctrl+shift+v"] in argvs
    assert ["xdotool", "key", "ctrl+v"] not in argvs


def test_paste_uses_ctrl_v_in_non_terminal():
    run, calls = _runner_with_window_class(
        'WM_CLASS(STRING) = "Navigator", "firefox"\n')
    inj = Injector(method="paste", runner=run, restore_delay=0)
    inj.inject("hello")
    argvs = [c[0] for c in calls]
    assert ["xdotool", "key", "ctrl+v"] in argvs
    assert ["xdotool", "key", "ctrl+shift+v"] not in argvs


def test_paste_falls_back_to_ctrl_v_when_class_lookup_fails():
    calls = []
    def run(cmd, **kw):
        if cmd[:2] == ["xdotool", "getactivewindow"]:
            raise RuntimeError("no active window")
        calls.append((cmd, kw))
        m = MagicMock(); m.stdout = b"OLD_CLIP"; m.returncode = 0
        return m
    inj = Injector(method="paste", runner=run, restore_delay=0)
    inj.inject("hello")
    assert ["xdotool", "key", "ctrl+v"] in [c[0] for c in calls]


def test_type_method_uses_xdotool_type():
    run, calls = _runner_recording()
    inj = Injector(method="type", runner=run)
    inj.inject("hi")
    argvs = [c[0] for c in calls]
    assert any(a[:2] == ["xdotool", "type"] and a[-1] == "hi" for a in argvs)


def test_delete_last_sends_backspaces():
    run, calls = _runner_recording()
    inj = Injector(method="type", runner=run)
    inj.delete_last(3)
    argvs = [c[0] for c in calls]
    assert ["xdotool", "key", "--repeat", "3", "BackSpace"] in argvs


def test_press_sends_key_chord():
    run, calls = _runner_recording()
    inj = Injector(method="type", runner=run)
    inj.press("Return")
    assert ["xdotool", "key", "Return"] in [c[0] for c in calls]
