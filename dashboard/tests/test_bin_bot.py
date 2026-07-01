import os, sys, importlib
import pytest

FRAMEWORK = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, FRAMEWORK)


@pytest.fixture
def bot(tmp_path, monkeypatch):
    monkeypatch.setenv("BAZA_BIN_DIR", str(tmp_path / "bin"))
    monkeypatch.setenv("BAZA_BIN_DB", str(tmp_path / "bin.db"))
    import dashboard.bin_store as bs
    importlib.reload(bs)
    bs.init_bin_db()
    import agents.bin.bin_bot as bb
    importlib.reload(bb)
    return bb


class _Doc:
    file_id = "F1"; file_name = "invoice.pdf"; file_unique_id = "u1"; file_size = 1234


class _Msg:
    photo = None; video = None; audio = None; voice = None
    document = _Doc()
    caption = "  a bill  "


def _fake_get_file(file_id, dest_path):
    with open(dest_path, "wb") as fh:
        fh.write(b"PDFBYTES")


def test_save_incoming_document(bot):
    item = bot.save_incoming(_Msg(), get_file=_fake_get_file, tg_user_id=42)
    assert item is not None
    assert item["name"] == "invoice.pdf" and item["kind"] == "document"
    assert item["caption"] == "a bill" and item["source"] == "telegram"
    assert os.path.isfile(item["stored_path"])


def test_save_incoming_no_file_returns_none(bot):
    class Empty:
        photo = document = video = audio = voice = None
        caption = ""
    assert bot.save_incoming(Empty(), get_file=_fake_get_file, tg_user_id=1) is None


def test_over_limit_document_refused(bot):
    class Big(_Doc):
        file_size = 25 * 1024 * 1024
    class Msg(_Msg):
        document = Big()
    assert bot.save_incoming(Msg(), get_file=_fake_get_file, tg_user_id=1) is None
