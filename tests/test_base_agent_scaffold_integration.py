"""Integration-level checks for the scaffold wiring in base_agent.

These avoid spinning up a real agent (Telegram/DB). They verify the building
blocks base_agent.handle_message calls when the flag is on, and that the flag is
off by default so live bots are unaffected."""
from core import scaffold_config
from core import skill_registry as reg
from core import skill_selector


def test_scaffold_enabled_fleet_wide():
    # Flipped on 2026-07-01 (Serge: "make the agents more capable"). If this
    # fails, someone turned the scaffold off — that should be a deliberate
    # decision, not config drift.
    scaffold_config.reload()
    assert scaffold_config.is_enabled("phil_hass") is True


def test_selector_block_built_from_manifest(tmp_path):
    shared = tmp_path / "shared"; shared.mkdir()
    (shared / "invoice_calculator.py").write_text(
        'SKILL_META={"category":"financial","summary":"Total an invoice.",'
        '"when_to_use":"total an invoice","args":{"items":"list"}}\n')
    jp = tmp_path / "m.json"; db = tmp_path / "m.db"
    reg.build(shared_dir=str(shared), agents_dir=str(tmp_path / "x"),
              out_json=str(jp), out_db=str(db), tools=None)
    sel = skill_selector.select("total this invoice", agent_id="phil_hass",
                                pinned=[], role_pins=[], top_k=5,
                                json_path=str(jp), db_path=str(db))
    block = skill_selector.render_block(sel)
    assert "RELEVANT SKILLS" in block and "invoice_calculator" in block


def test_base_agent_imports_with_scaffold():
    # Importing base_agent must not fail (catches circular-import from the
    # scaffold_config import added to the module header).
    import importlib
    import core.base_agent as ba
    importlib.reload(ba)
    assert hasattr(ba, "BaseAgent")
