import json
import os
import sys
import tempfile
import pytest


@pytest.fixture()
def tmp_settings(monkeypatch):
    d = tempfile.mkdtemp(prefix="ss_")
    monkeypatch.setenv("BAZA_SOCIAL_SETTINGS_DIR", d)
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dashboard"))
    if "social_settings" in sys.modules:
        del sys.modules["social_settings"]
    yield d
    if "social_settings" in sys.modules:
        del sys.modules["social_settings"]


def test_load_settings_creates_default_file(tmp_settings):
    import social_settings
    s = social_settings.load_settings()
    assert s["default_copy_model"] == "gpt-oss:20b"
    assert s["autopilot_master"] is False
    assert os.path.exists(os.path.join(tmp_settings, "social_settings.json"))


def test_save_settings_round_trip(tmp_settings):
    import social_settings
    s = social_settings.load_settings()
    s["daily_post_cap"] = 7
    social_settings.save_settings(s)
    s2 = social_settings.load_settings()
    assert s2["daily_post_cap"] == 7


def test_load_brand_kit_creates_default(tmp_settings):
    import social_settings
    b = social_settings.load_brand_kit()
    assert b["primary_color"].startswith("#")
    assert "#allhomebuilding" in b["hashtag_floor"]


def test_load_prompt_returns_content(tmp_settings):
    import social_settings
    p = social_settings.load_prompt("caption_system")
    assert isinstance(p, str) and len(p) > 20
