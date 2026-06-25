"""Smoke test against the real built manifest. Skips if the manifest hasn't
been built in this environment (so CI without a build step stays green)."""
import os
import pytest
from core import skill_registry as reg


def _require_manifest():
    if not os.path.exists(reg.DEFAULT_JSON):
        pytest.skip("manifest not built in this environment")


def test_real_manifest_has_core_skills():
    _require_manifest()
    assert reg.get("artifact_save") is not None
    assert reg.get("invoice_calculator") is not None
    assert reg.get("skill_search") is not None     # our new meta-skill self-registers
    assert reg.get("call_tool") is not None         # our new bridge self-registers


def test_real_manifest_categories_populated():
    _require_manifest()
    cats = reg.categories()
    assert cats.get("financial", 0) >= 5
    assert sum(cats.values()) >= 200                # whole skill library indexed


def test_real_manifest_search_finds_invoice():
    _require_manifest()
    hits = reg.search("overdue invoice", top_k=5)
    assert any("invoice" in h["name"] for h in hits)
