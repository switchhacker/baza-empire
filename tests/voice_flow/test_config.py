import textwrap
from voice_flow.config import load_config


def test_load_defaults_from_packaged_yaml():
    cfg = load_config()  # no path → packaged voice_flow/config.yaml
    assert cfg.hotkeys["raw"] == "ctrl+space"
    assert cfg.stt["model"] == "base"
    assert cfg.agent["default_agent"] == "specter_voss"
    assert cfg.injection["method"] == "paste"


def test_load_custom_path(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(textwrap.dedent("""
        hotkeys: {raw: "alt+d"}
        stt: {model: "small"}
        flow: {}
        agent: {}
        audio: {}
        injection: {method: "type"}
        commands: {}
    """))
    cfg = load_config(str(p))
    assert cfg.hotkeys["raw"] == "alt+d"
    assert cfg.injection["method"] == "type"


def test_reload_if_changed(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("hotkeys: {raw: a}\nstt: {}\nflow: {}\nagent: {}\naudio: {}\ninjection: {}\ncommands: {}\n")
    cfg = load_config(str(p))
    assert cfg.reload_if_changed() is False
    import os, time
    time.sleep(0.01)
    p.write_text("hotkeys: {raw: b}\nstt: {}\nflow: {}\nagent: {}\naudio: {}\ninjection: {}\ncommands: {}\n")
    os.utime(str(p), None)
    assert cfg.reload_if_changed() is True
    assert cfg.hotkeys["raw"] == "b"
