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

def test_per_agent_null_entry_does_not_crash(tmp_path, monkeypatch):
    # A per_agent key with no value parses to None — is_enabled must not raise.
    cfg = tmp_path / "scaffold.yaml"
    cfg.write_text(textwrap.dedent("""
        scaffold:
          enabled: true
          per_agent:
            rex_valor:
    """))
    monkeypatch.setattr(scaffold_config, "_CONFIG_PATH", str(cfg))
    scaffold_config.reload()
    # No 'enabled' override present → falls back to global (True)
    assert scaffold_config.is_enabled("rex_valor") is True

def test_missing_config_file_uses_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(scaffold_config, "_CONFIG_PATH", str(tmp_path / "nope.yaml"))
    scaffold_config.reload()
    assert scaffold_config.is_enabled() is False
    assert scaffold_config.max_steps() == 6
    assert scaffold_config.pinned_core() == ["artifact_save", "web_search",
                                             "ahb123_query", "skill_search", "call_tool"]
