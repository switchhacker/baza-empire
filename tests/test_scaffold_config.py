import os, textwrap
from core import scaffold_config

def test_disabled_by_default(tmp_path, monkeypatch):
    cfg = tmp_path / "scaffold.yaml"
    cfg.write_text("scaffold:\n  enabled: false\n")
    monkeypatch.setattr(scaffold_config, "_CONFIG_PATH", str(cfg))
    scaffold_config.reload()
    assert scaffold_config.is_enabled("phil_hass") is False
    assert scaffold_config.max_steps() == 6
    assert scaffold_config.retrieval_top_k() == 8

def test_per_agent_override(tmp_path, monkeypatch):
    cfg = tmp_path / "scaffold.yaml"
    cfg.write_text(textwrap.dedent("""
        scaffold:
          enabled: false
          per_agent:
            claw_batto:
              enabled: true
    """))
    monkeypatch.setattr(scaffold_config, "_CONFIG_PATH", str(cfg))
    scaffold_config.reload()
    assert scaffold_config.is_enabled("claw_batto") is True
    assert scaffold_config.is_enabled("phil_hass") is False

def test_pinned_core_list(tmp_path, monkeypatch):
    cfg = tmp_path / "scaffold.yaml"
    cfg.write_text("scaffold:\n  enabled: true\n  pinned_core: [artifact_save, call_tool]\n")
    monkeypatch.setattr(scaffold_config, "_CONFIG_PATH", str(cfg))
    scaffold_config.reload()
    assert scaffold_config.pinned_core() == ["artifact_save", "call_tool"]
