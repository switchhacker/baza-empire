import os, importlib
import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("BAZA_BIN_DIR", str(tmp_path / "bin"))
    monkeypatch.setenv("BAZA_BIN_DB", str(tmp_path / "bin.db"))
    import dashboard.bin_store as bs
    importlib.reload(bs)
    bs.init_bin_db()
    import dashboard.app as app
    return bs, app


def test_recent_images_include_bin(env):
    bs, app = env
    bs.add_file(filename="fromphone.jpg", data=b"IMG", source="telegram")
    imgs = app._pick_list_images(limit=60, agent_filter="", include_private=True)
    names = [i["name"] for i in imgs]
    assert "fromphone.jpg" in names
    hit = next(i for i in imgs if i["name"] == "fromphone.jpg")
    assert hit["token"].startswith("~") and hit["agent_id"] == "bin"
